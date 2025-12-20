import os
import asyncio
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.bgg_game import BGGGame
from app.models.bgg_plays import BGGPlay
from app.services.bgg.auth_session import BGGAuthSessionManager
from app.utils.logging import log_info, log_success

USER_AGENT = os.getenv("USER_AGENT", "bgg-api/1.0 (+https://railway.app)")
BGG_PLAYS_URL = "https://boardgamegeek.com/geekplay.php"

# BGG can rate-limit; keep it gentle
DEFAULT_DELAY_SECONDS = float(os.getenv("BGG_PLAYS_DELAY_SECONDS", "0.8"))
DEFAULT_SHOWCOUNT = int(os.getenv("BGG_PLAYS_SHOWCOUNT", "1000"))


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


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        s = str(value).strip()
        if s == "":
            return None
        return int(float(s))
    except Exception:
        return None


def _to_bool01(value: Any) -> Optional[bool]:
    # BGG typically uses "0"/"1" strings
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("1", "true", "yes"):
        return True
    if s in ("0", "false", "no"):
        return False
    return None


def _extract_comments(play: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    c = play.get("comments")
    if isinstance(c, dict):
        return c.get("value"), c.get("rendered")
    if isinstance(c, str):
        return c, c
    return None, None


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


def _play_to_model_data(play: Dict[str, Any]) -> Dict[str, Any]:
    comments_value, comments_rendered = _extract_comments(play)

    data: Dict[str, Any] = {
        "play_id": _to_int(play.get("playid")),
        "user_id": _to_int(play.get("userid")),
        "object_type": play.get("objecttype"),
        "object_id": _to_int(play.get("objectid")),
        "tstamp": play.get("tstamp"),
        "play_date": play.get("playdate"),
        "quantity": _to_int(play.get("quantity")),
        "length": _to_int(play.get("length")),
        "location": play.get("location"),
        "num_players": _to_int(play.get("numplayers")),
        "length_ms": _to_int(play.get("length_ms")),
        "comments_value": comments_value,
        "comments_rendered": comments_rendered,
        "incomplete": _to_bool01(play.get("incomplete")),
        "now_in_stats": _to_bool01(play.get("nowinstats")),
        "win_state": play.get("winstate"),
        "online": _to_bool01(play.get("online")),
        "game_name": play.get("name"),
        "players": play.get("players"),
        "subtypes": play.get("subtypes"),
        "raw": play,
    }

    return data


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


async def update_bgg_plays_from_collection() -> Dict[str, Any]:
    """
    Cross-reference rule:
    - We only fetch plays for games currently present in our DB collection (BGGGame).
    - For each bgg_id, call BGG plays endpoint and upsert by play_id.
    """
    log_info("📅 Rozpoczynam pobieranie plays z BGG (per gra z kolekcji w DB)")

    auth = BGGAuthSessionManager()
    inserted_total = 0
    updated_total = 0
    games_total = 0

    async with _make_client() as client:
        # 1) load current collection bgg_ids from DB
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(BGGGame.bgg_id, BGGGame.title).order_by(BGGGame.bgg_id.asc())
            )
            games = [(row[0], row[1]) for row in res.all() if row[0] is not None]

        games_total = len(games)

        # 2) iterate and fetch plays
        for idx, (bgg_id, title) in enumerate(games, start=1):
            game_label = title or f"bgg_id={bgg_id}"
            log_info(f"[{idx}/{games_total}] 🎲 Plays: pobieram dla gry: {game_label}")

            try:
                plays = await fetch_all_plays_for_game(client, auth, bgg_id=bgg_id, showcount=DEFAULT_SHOWCOUNT)

                if not plays:
                    await asyncio.sleep(DEFAULT_DELAY_SECONDS)
                    continue

                # 3) upsert plays
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        for p in plays:
                            data = _play_to_model_data(p)

                            # ensure object_id matches current bgg_id even if missing/strange
                            data["object_id"] = bgg_id

                            inserted = await upsert_play(session, data)
                            if inserted:
                                inserted_total += 1
                            else:
                                updated_total += 1

                await asyncio.sleep(DEFAULT_DELAY_SECONDS)

            except httpx.HTTPStatusError as e:
                log_info(f"⚠️ Plays HTTP error for bgg_id={bgg_id}: {e.response.status_code}")
                await asyncio.sleep(DEFAULT_DELAY_SECONDS * 2)
            except Exception as e:
                log_info(f"⚠️ Plays error for bgg_id={bgg_id}: {type(e).__name__}: {e}")
                await asyncio.sleep(DEFAULT_DELAY_SECONDS * 2)

    log_success(
        f"✅ Plays import zakończony. Games: {games_total}, Inserted: {inserted_total}, Updated: {updated_total}"
    )
    return {
        "games": games_total,
        "inserted": inserted_total,
        "updated": updated_total,
    }