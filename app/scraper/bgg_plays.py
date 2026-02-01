import os
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, cast

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.bgg_game import BGGGame
from app.models.bgg_plays import BGGPlay
from app.services.bgg.auth_session import BGGAuthSessionManager
from app.utils.logging import log_info, log_success
from app.utils.convert import to_bool, to_int
from app.utils.telegram_notify import send_scrape_message

USER_AGENT = os.getenv("USER_AGENT", "bgg-api/1.0 (+https://railway.app)")
BGG_PLAYS_URL = "https://boardgamegeek.com/geekplay.php"


# =============================================================================
# CONFIGURATION
# =============================================================================

# BGG can rate-limit; keep it gentle
DEFAULT_DELAY_SECONDS = float(os.getenv("BGG_PLAYS_DELAY_SECONDS", "1.2"))
DEFAULT_SHOWCOUNT = int(os.getenv("BGG_PLAYS_SHOWCOUNT", "600"))
PLAY_CONCURRENCY = int(os.getenv("BGG_PLAYS_CONCURRENCY", "1"))


# =============================================================================
# HTTP HELPERS
# =============================================================================

def _default_headers() -> Dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_default_headers(),
        follow_redirects=True,
        http2=True,
        timeout=httpx.Timeout(30.0),
    )


# =============================================================================
# COMMENT HELPERS
# =============================================================================

def _extract_comments(play: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    c = play.get("comments")
    if isinstance(c, dict):
        return c.get("value"), c.get("rendered")
    if isinstance(c, str):
        return c, c
    return None, None


# =============================================================================
# PLAYS FETCHING HELPERS
# =============================================================================

async def fetch_bgg_plays_page(
    client: httpx.AsyncClient,
    auth: BGGAuthSessionManager,
    bgg_id: int,
    page_id: int,
    showcount: int = DEFAULT_SHOWCOUNT,
) -> Dict[str, Any]:
    """
    Fetch a single plays JSON page for a given game (objectid).
    Requires authenticated cookies.
    """
    await auth.ensure_session(client)

    params = {
        "action": "getplays",
        "ajax": "1",
        "currentUser": "true",
        "objectid": str(bgg_id),
        "objecttype": "thing",
        "pageID": str(page_id),
        "showcount": str(showcount),
    }

    resp = await client.get(BGG_PLAYS_URL, params=params)
    resp.raise_for_status()
    return resp.json()


# =============================================================================
# PLAYS TRANSFORMATION HELPERS
# =============================================================================

def _play_to_model_data(play: Dict[str, Any]) -> Dict[str, Any]:
    comments_value, comments_rendered = _extract_comments(play)

    data: Dict[str, Any] = {
        "play_id": to_int(play.get("playid")),
        "user_id": to_int(play.get("userid")),
        "object_type": play.get("objecttype"),
        "object_id": to_int(play.get("objectid")),
        "tstamp": play.get("tstamp"),
        "play_date": play.get("playdate"),
        "quantity": to_int(play.get("quantity")),
        "length": to_int(play.get("length")),
        "location": play.get("location"),
        "num_players": to_int(play.get("numplayers")),
        "length_ms": to_int(play.get("length_ms")),
        "comments_value": comments_value,
        "comments_rendered": comments_rendered,
        "incomplete": to_bool(play.get("incomplete")),
        "now_in_stats": to_bool(play.get("nowinstats")),
        "win_state": play.get("winstate"),
        "online": to_bool(play.get("online")),
        "game_name": play.get("name"),
        "players": play.get("players"),
        "subtypes": play.get("subtypes"),
        "raw": play,
    }

    return data


# =============================================================================
# DATA PERSISTENCE
# =============================================================================

async def upsert_play(session, data: Dict[str, Any]) -> bool:
    """
    Upsert by unique play_id.
    Returns True if inserted, False if updated.
    """
    play_id = data.get("play_id")
    if not play_id:
        return False  # skip invalid

    res = await session.execute(select(BGGPlay).where(BGGPlay.play_id == play_id))
    existing = res.scalar_one_or_none()

    if existing:
        # update fields (do not touch primary key)
        for k, v in data.items():
            if k in ("id",):
                continue
            setattr(existing, k, v)
        return False

    session.add(BGGPlay(**data))
    return True


async def fetch_all_plays_for_game(
    client: httpx.AsyncClient,
    auth: BGGAuthSessionManager,
    bgg_id: int,
    showcount: int = DEFAULT_SHOWCOUNT,
    max_pages: int = 200,
) -> List[Dict[str, Any]]:
    """
    Fetch all pages of plays for a game until empty page is reached.
    """
    all_plays: List[Dict[str, Any]] = []
    page = 1

    while page <= max_pages:
        payload = await fetch_bgg_plays_page(client, auth, bgg_id=bgg_id, page_id=page, showcount=showcount)
        plays = payload.get("plays") or []
        if not plays:
            break

        all_plays.extend(plays)

        # if returned fewer than showcount, likely last page
        if len(plays) < showcount:
            break

        page += 1
        await asyncio.sleep(DEFAULT_DELAY_SECONDS)

    return all_plays


# =============================================================================
# SYNC HELPERS
# =============================================================================

async def _sync_game_plays(
    client: httpx.AsyncClient,
    auth: BGGAuthSessionManager,
    sem: asyncio.Semaphore,
    idx: int,
    total: int,
    bgg_id: int,
    title: str | None,
) -> Dict[str, Any]:
    game_label = title or f"bgg_id={bgg_id}"
    inserted = 0
    updated = 0
    inserted_titles: List[str] = []
    updated_titles: List[str] = []

    async with sem:
        log_info(f"[{idx}/{total}] 🎲 Plays: pobieram dla gry: {game_label}")
        try:
            plays = await fetch_all_plays_for_game(client, auth, bgg_id=bgg_id, showcount=DEFAULT_SHOWCOUNT)
        except httpx.HTTPStatusError as e:
            log_info(f"⚠️ Plays HTTP error for bgg_id={bgg_id}: {e.response.status_code}")
            await asyncio.sleep(DEFAULT_DELAY_SECONDS * 2)
            return {"inserted": 0, "updated": 0}
        except Exception as e:
            log_info(f"⚠️ Plays error for bgg_id={bgg_id}: {type(e).__name__}: {e}")
            await asyncio.sleep(DEFAULT_DELAY_SECONDS * 2)
            return {"inserted": 0, "updated": 0}

        if not plays:
            await asyncio.sleep(DEFAULT_DELAY_SECONDS)
            return {"inserted": 0, "updated": 0}

        session = AsyncSessionLocal()
        session = cast(AsyncSession, session)
        try:
            async with session.begin():
                for p in plays:
                    data = _play_to_model_data(p)
                    data["object_id"] = bgg_id
                    inserted_flag = await upsert_play(session, data)
                    if inserted_flag:
                        inserted += 1
                        inserted_titles.append(game_label)
                    else:
                        updated += 1
                        updated_titles.append(game_label)
        finally:
            await session.close()

        await asyncio.sleep(DEFAULT_DELAY_SECONDS)
        return {
            "inserted": inserted,
            "updated": updated,
            "inserted_titles": inserted_titles,
            "updated_titles": updated_titles,
        }


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

async def update_bgg_plays_from_collection() -> Dict[str, Any]:
    log_info("📅 Rozpoczynam pobieranie plays z BGG (per gra z kolekcji w DB)")
    start_time = datetime.utcnow()

    auth = BGGAuthSessionManager()
    inserted_total = 0
    updated_total = 0
    inserted_titles: List[str] = []
    updated_titles: List[str] = []

    async with _make_client() as client:
        session = AsyncSessionLocal()
        session = cast(AsyncSession, session)
        try:
            res = await session.execute(
                select(BGGGame.bgg_id, BGGGame.title).order_by(BGGGame.bgg_id.asc())
            )
            games = [(row[0], row[1]) for row in res.all() if row[0] is not None]
        finally:
            await session.close()
        games_total = len(games)
        sem = asyncio.Semaphore(PLAY_CONCURRENCY)
        tasks = [
            _sync_game_plays(client, auth, sem, idx, games_total, bgg_id, title)
            for idx, (bgg_id, title) in enumerate(games, start=1)
        ]

        results = await asyncio.gather(*tasks)
        for result in results:
            inserted_total += result.get("inserted", 0)
            updated_total += result.get("updated", 0)
            inserted_titles.extend(result.get("inserted_titles", []))
            updated_titles.extend(result.get("updated_titles", []))

    log_success(
        f"✅ Plays import zakończony. Games: {games_total}, Inserted: {inserted_total}, Updated: {updated_total}"
    )
    end_time = datetime.utcnow()
    total_plays = inserted_total + updated_total
    stats = {
        "Total games": games_total,
        "Plays processed": total_plays,
        "New plays": inserted_total,
        "Updated plays": updated_total,
    }
    details = {
        "New plays": inserted_titles,
        "Updated plays": updated_titles,
    }
    await send_scrape_message("BGG plays sync", "✅ SUCCESS", start_time, end_time, stats, details)
    return {
        "games": games_total,
        "inserted": inserted_total,
        "updated": updated_total,
    }
