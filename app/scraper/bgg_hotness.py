# app/scraper/bgg_hotness.py

import importlib.util
import os
import random
import asyncio
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.database import AsyncSessionLocal  # pomocnicze, jeżeli kiedyś zapiszesz dane do DB
from app.models.bgg_hotness import BGGHotGame, BGGHotPerson
from sqlalchemy import select  # nadal dostępne dla ewentualnych zapytań
from app.utils.convert import to_float, to_int
from app.utils.logging import log_info, log_success, log_error, log_warning
from app.utils.telegram_notify import send_scrape_message


# =============================================================================
# CONFIGURATION
# =============================================================================

BGG_XML_BASE = "https://boardgamegeek.com/xmlapi2"
HOT_GAMES_URL = f"{BGG_XML_BASE}/hot?type=boardgame"
HOT_PERSONS_URL = f"{BGG_XML_BASE}/hot?type=boardgameperson"
THING_URL_TMPL = f"{BGG_XML_BASE}/thing?id={{bgg_id}}&stats=1"

BGG_API_TOKEN = os.getenv("BGG_API_TOKEN")
USER_AGENT = "BoardGamesApp/1.0 (+contact: your-email@example.com)"
HOTNESS_DETAIL_CONCURRENCY = int(os.getenv("BGG_HOTNESS_DETAIL_CONCURRENCY", "1"))
HOTNESS_DETAIL_PAUSE_SECONDS = float(os.getenv("BGG_HOTNESS_DETAIL_PAUSE_SECONDS", "1.5"))
BGG_REQUEST_PAUSE_SECONDS = float(os.getenv("BGG_REQUEST_PAUSE_SECONDS", "0.8"))
BGG_REQUEST_JITTER_SECONDS = float(os.getenv("BGG_REQUEST_JITTER_SECONDS", "0.2"))
BGG_REQUEST_BACKOFF_FACTOR = float(os.getenv("BGG_REQUEST_BACKOFF_FACTOR", "1.5"))


# =============================================================================
# HTTP HELPERS
# =============================================================================

def _default_headers() -> Dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if BGG_API_TOKEN:
        headers["Authorization"] = f"Bearer {BGG_API_TOKEN}"
    return headers


def _make_client() -> httpx.AsyncClient:
    http2_flag = os.getenv("HTTP2", "1") == "1"

    return httpx.AsyncClient(
        headers=_default_headers(),
        follow_redirects=True,
        http2=http2_flag,
        timeout=httpx.Timeout(30.0),
    )


# =============================================================================
# RETRY / BACKOFF HANDLING
# =============================================================================

async def fetch_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    log_info(f"➡️ Fetching XML from: {url}")

    base_delay = 1.0
    max_attempts = 12
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = await client.get(url)

            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                await asyncio.sleep(BGG_REQUEST_PAUSE_SECONDS + random.uniform(0, BGG_REQUEST_JITTER_SECONDS))
                return root

            if resp.status_code == 202:
                delay = float(resp.headers.get("Retry-After", base_delay * (BGG_REQUEST_BACKOFF_FACTOR ** (attempt - 1))))
                log_warning(f"⏳ 202 Accepted — czekam {delay:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(delay)
                continue

            if resp.status_code == 429:
                delay = base_delay * (BGG_REQUEST_BACKOFF_FACTOR ** (attempt - 1))
                jitter = random.uniform(0, BGG_REQUEST_JITTER_SECONDS)
                log_warning(f"🚦 429 Too Many Requests — retry za {delay + jitter:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(delay + jitter)
                continue

            if resp.status_code in (500, 502, 503, 504):
                delay = base_delay * (BGG_REQUEST_BACKOFF_FACTOR ** (attempt - 1))
                log_warning(f"🛠 {resp.status_code} — retry za {delay:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(delay)
                continue

            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"BGG auth error {resp.status_code}. Sprawdź BGG_API_TOKEN i zatwierdzenie aplikacji."
                )

            resp.raise_for_status()
        except Exception as exc:
            last_exc = exc
            delay = base_delay * attempt
            log_warning(f"⚠️ {type(exc).__name__}: {exc} — retry za {delay:.1f}s (attempt {attempt}/{max_attempts})")
            await asyncio.sleep(delay)

    if last_exc:
        raise last_exc
    raise RuntimeError("Niepowodzenie pobierania z BGG bez konkretnego wyjątku.")


# =============================================================================
# PARSING HELPERS
# =============================================================================

def _link_values(item: ET.Element, link_type: str) -> List[str]:
    values: List[str] = []
    for link in item.findall("link"):
        if link.get("type") == link_type:
            value = link.get("value")
            if value:
                values.append(value)
    return values


def _child_attrib(element: Optional[ET.Element], path: str, attr: str = "value") -> Optional[str]:
    if element is None:
        return None
    child = element.find(path)
    if child is None:
        return None
    return child.attrib.get(attr)


def _child_text(element: Optional[ET.Element], path: str) -> Optional[str]:
    if element is None:
        return None
    child = element.find(path)
    if child is None or child.text is None:
        return None
    return child.text.strip()


# =============================================================================
# HOTNESS GAMES
# =============================================================================

def extract_hot_game(item: ET.Element) -> Dict[str, Any]:
    return {
        "bgg_id": to_int(item.get("id")),
        "rank": to_int(item.get("rank")),
        "name": _child_attrib(item, "name") or "",
        "year_published": to_int(_child_attrib(item, "yearpublished")),
        "bgg_url": f"https://boardgamegeek.com/boardgame/{item.get('id')}",
        "last_modified": datetime.utcnow(),
    }


def extract_hot_game_details(item: ET.Element) -> Dict[str, Any]:
    stats_el = item.find("statistics/ratings")
    average_weight = to_float(_child_attrib(stats_el, "averageweight")) if stats_el is not None else None
    bgg_rating = to_float(_child_attrib(stats_el, "average")) if stats_el is not None else None

    name = None
    for name_el in item.findall("name"):
        if name_el.get("type") == "primary":
            name = name_el.get("value")
            break

    return {
        "original_title": name,
        "description": item.findtext("description"),
        "image": _child_text(item, "image"),
        "mechanics": _link_values(item, "boardgamemechanic"),
        "designers": _link_values(item, "boardgamedesigner"),
        "artists": _link_values(item, "boardgameartist"),
        "min_players": to_int(_child_attrib(item, "minplayers")),
        "max_players": to_int(_child_attrib(item, "maxplayers")),
        "min_playtime": to_int(_child_attrib(item, "minplaytime")),
        "max_playtime": to_int(_child_attrib(item, "maxplaytime")),
        "play_time": to_int(_child_attrib(item, "playingtime")),
        "min_age": to_int(_child_attrib(item, "minage")),
        "type": item.get("type"),
        "weight": average_weight,
        "bgg_rating": bgg_rating,
    }


async def _build_hot_game_payload(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    idx: int,
    total: int,
    base_game: Dict[str, Any],
) -> Dict[str, Any]:
    bgg_id = base_game["bgg_id"]
    detail_url = THING_URL_TMPL.format(bgg_id=bgg_id)
    async with sem:
        log_info(f"[{idx}/{total}] 🔥 {base_game.get('name')} (rank {base_game.get('rank')})")
        try:
            detail_root = await fetch_xml(client, detail_url)
            detail_item = detail_root.find("item")
            if detail_item is not None:
                base_game.update(extract_hot_game_details(detail_item))
        except Exception as exc:
            log_warning(f"⚠️ Szczegóły gry {bgg_id} nie zostały pobrane: {exc}")

    await asyncio.sleep(HOTNESS_DETAIL_PAUSE_SECONDS)
    return base_game


async def fetch_bgg_hotness_games() -> List[Dict[str, Any]]:
    start_time = datetime.utcnow()
    log_info("🎲 Rozpoczynam pobieranie Hotness Games z BGG")
    try:
        async with _make_client() as client:
            root = await fetch_xml(client, HOT_GAMES_URL)
            items = root.findall("item")
            base_games = [extract_hot_game(item) for item in items]
            sem = asyncio.Semaphore(HOTNESS_DETAIL_CONCURRENCY)
            tasks = [
                _build_hot_game_payload(client, sem, idx, len(base_games), game)
                for idx, game in enumerate(base_games, start=1)
            ]

            games = await asyncio.gather(*tasks)
            log_success(f"🎲 Zakończono przetwarzanie {len(games)} hotness gier")
            top_games: List[str] = [str(game.get("name") or game.get("title") or "Untitled") for game in games[:10]]
            details = {"Top games": top_games}
            stats = {"Games": len(games)}
            end_time = datetime.utcnow()
            await send_scrape_message("BGG hotness games", "✅ SUCCESS", start_time, end_time, stats, details)
            return games
    except (httpx.HTTPError, ET.ParseError) as exc:
        log_error(f"❌ Błąd podczas pobierania hotness gier: {exc}")
    except Exception as exc:
        log_error(f"❌ Nieoczekiwany błąd przy hotness games: {exc}")
    return []


# =============================================================================
# HOTNESS PERSONS
# =============================================================================

def extract_hot_person(item: ET.Element) -> Dict[str, Any]:
    return {
        "bgg_id": to_int(item.get("id")),
        "rank": to_int(item.get("rank")),
        "name": _child_attrib(item, "name") or "",
        "image": _child_attrib(item, "thumbnail"),
        "bgg_url": f"https://boardgamegeek.com/boardgamedesigner/{item.get('id')}",
        "last_modified": datetime.utcnow(),
    }


async def fetch_bgg_hotness_persons() -> List[Dict[str, Any]]:
    start_time = datetime.utcnow()
    log_info("👤 Rozpoczynam pobieranie Hotness Persons z BGG")
    try:
        async with _make_client() as client:
            root = await fetch_xml(client, HOT_PERSONS_URL)
            items = root.findall("item")
            persons = [extract_hot_person(item) for item in items]
            log_success(f"👤 Zakończono przetwarzanie {len(persons)} hotness osób")
            top_persons: List[str] = [str(person.get("name") or "Unknown") for person in persons[:10]]
            details = {"Top persons": top_persons}
            stats = {"Persons": len(persons)}
            end_time = datetime.utcnow()
            await send_scrape_message("BGG hotness persons", "✅ SUCCESS", start_time, end_time, stats, details)
            return persons
    except (httpx.HTTPError, ET.ParseError) as exc:
        log_error(f"❌ Błąd podczas pobierania hotness osób: {exc}")
    except Exception as exc:
        log_error(f"❌ Nieoczekiwany błąd przy hotness persons: {exc}")
    return []
