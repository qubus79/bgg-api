# app/scraper/bgg_hotness.py

import os
import asyncio
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any
from app.database import AsyncSessionLocal  # pozostawione jeśli kiedyś zapiszesz do DB z tego modułu
from app.models.bgg_hotness import BGGHotGame, BGGHotPerson
from sqlalchemy import select  # jw.
from app.utils.logging import log_info, log_success, log_error, log_warning

# --- Konfiguracja / BGG ---
BGG_XML_BASE = "https://boardgamegeek.com/xmlapi2"
HOT_GAMES_URL = f"{BGG_XML_BASE}/hot?type=boardgame"
HOT_PERSONS_URL = f"{BGG_XML_BASE}/hot?type=boardgameperson"
THING_URL_TMPL = f"{BGG_XML_BASE}/thing?id={{bgg_id}}&stats=1"

BGG_API_TOKEN = os.getenv("BGG_API_TOKEN")
USER_AGENT = "BoardGamesApp/1.0 (+contact: your-email@example.com)"

def _default_headers() -> Dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if BGG_API_TOKEN:
        headers["Authorization"] = f"Bearer {BGG_API_TOKEN}"
    return headers

def _make_client() -> httpx.AsyncClient:
    """HTTP/2 włączone automatycznie, jeśli pakiet h2 jest dostępny (lub ustawisz HTTP2=1 i masz h2)."""
    want_http2 = os.getenv("HTTP2", "1") == "1"
    try:
        if want_http2:
            import h2  # noqa: F401
        http2_flag = want_http2
    except ImportError:
        http2_flag = False

    return httpx.AsyncClient(
        headers=_default_headers(),
        follow_redirects=True,
        http2=http2_flag,
        timeout=httpx.Timeout(30.0),
    )

# ----------------------------
# 🌐 XML Fetching with Retry
# ----------------------------
async def fetch_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    """
    Pobiera XML z obsługą:
    - 202 Accepted + Retry-After (kolejka na BGG),
    - 429 Too Many Requests + Retry-After,
    - 5xx z backoffem,
    - 401/403 (błąd autoryzacji).
    """
    log_info(f"➡️ Fetching XML from: {url}")

    base_delay = 1.0
    max_attempts = 12
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = await client.get(url)

            if resp.status_code == 200:
                return ET.fromstring(resp.text)

            if resp.status_code == 202:
                delay_hdr = resp.headers.get("Retry-After")
                sleep_s = float(delay_hdr) if delay_hdr else base_delay * attempt
                log_info(f"⏳ 202 Accepted — czekam {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_s)
                continue

            if resp.status_code == 429:
                delay_hdr = resp.headers.get("Retry-After")
                sleep_s = float(delay_hdr) if delay_hdr else base_delay * attempt
                log_warning(f"🚦 429 Too Many Requests — retry za {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_s)
                continue

            if resp.status_code in (500, 502, 503, 504):
                sleep_s = base_delay * attempt
                log_warning(f"🛠 {resp.status_code} — retry za {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_s)
                continue

            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"BGG auth error {resp.status_code}. "
                    "Sprawdź BGG_API_TOKEN i czy aplikacja na BGG jest zatwierdzona."
                )

            resp.raise_for_status()

        except Exception as e:
            last_exc = e
            sleep_s = base_delay * attempt
            log_warning(f"⚠️ {type(e).__name__}: {e} — retry za {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
            await asyncio.sleep(sleep_s)

    if last_exc:
        raise last_exc
    raise RuntimeError("Niepowodzenie pobierania z BGG bez konkretnego wyjątku.")

# ----------------------------
# 🟣 HOTNESS GAMES
# ----------------------------
def extract_hot_game(item: ET.Element) -> Dict[str, Any]:
    return {
        "bgg_id": int(item.attrib["id"]),
        "rank": int(item.attrib.get("rank", 0)),
        "name": item.find("name").attrib.get("value", "") if item.find("name") is not None else "",
        "year_published": int(item.find("yearpublished").attrib.get("value", 0)) if item.find("yearpublished") is not None else None,
        "bgg_url": f"https://boardgamegeek.com/boardgame/{item.attrib['id']}",
        "last_modified": datetime.utcnow(),
    }

def extract_hot_game_details(item: ET.Element) -> Dict[str, Any]:
    links = item.findall("link")
    stats_el = item.find("statistics/ratings")
    average_weight = None
    if stats_el is not None and stats_el.find("averageweight") is not None:
        try:
            average_weight = float(stats_el.find("averageweight").attrib.get("value"))
        except Exception:
            pass

    name = None
    for name_el in item.findall("name"):
        if name_el.attrib.get("type") == "primary":
            name = name_el.attrib.get("value")
            break

    return {
        "original_title": name,
        "description": item.findtext("description"),
        "image": (item.find("image").text.strip() if item.find("image") is not None and item.find("image").text else None),
        "mechanics": [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgamemechanic"],
        "designers": [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgamedesigner"],
        "artists": [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgameartist"],
        "min_players": int(item.find("minplayers").attrib.get("value", 0)) if item.find("minplayers") is not None else None,
        "max_players": int(item.find("maxplayers").attrib.get("value", 0)) if item.find("maxplayers") is not None else None,
        "min_playtime": int(item.find("minplaytime").attrib.get("value", 0)) if item.find("minplaytime") is not None else None,
        "max_playtime": int(item.find("maxplaytime").attrib.get("value", 0)) if item.find("maxplaytime") is not None else None,
        "play_time": int(item.find("playingtime").attrib.get("value", 0)) if item.find("playingtime") is not None else None,
        "min_age": int(item.find("minage").attrib.get("value", 0)) if item.find("minage") is not None else None,
        "type": item.attrib.get("type", None),
        "weight": average_weight,
    }

async def fetch_bgg_hotness_games() -> List[Dict[str, Any]]:
    log_info("🎲 Rozpoczynam pobieranie Hotness Games z BGG")
    games: List[Dict[str, Any]] = []
    try:
        async with _make_client() as client:
            root = await fetch_xml(client, HOT_GAMES_URL)
            items = root.findall("item")

            for idx, item in enumerate(items, start=1):
                game = extract_hot_game(item)
                bgg_id = game["bgg_id"]
                detail_url = THING_URL_TMPL.format(bgg_id=bgg_id)
                log_info(f"[{idx}/{len(items)}] 🔥 Rank {game['rank']} - {game['name']}")
                detail_root = await fetch_xml(client, detail_url)
                detail_item = detail_root.find("item")
                if detail_item:
                    game.update(extract_hot_game_details(detail_item))
                games.append(game)

                # grzecznościowa pauza między /thing
                await asyncio.sleep(1.5)

            log_success(f"🎲 Zakończono przetwarzanie {len(games)} hotness gier")
            return games

    except httpx.HTTPError as e:
        log_error(f"❌ HTTP error while fetching hot games: {e}")
    except ET.ParseError as e:
        log_error(f"❌ XML parsing error for hot games: {e}")
    except Exception as e:
        log_error(f"❌ Unexpected error while parsing hot games: {e}")
    return []

# ----------------------------
# 🟡 HOTNESS PERSONS
# ----------------------------
def extract_hot_person(item: ET.Element) -> Dict[str, Any]:
    return {
        "bgg_id": int(item.attrib["id"]),
        "rank": int(item.attrib.get("rank", 0)),
        "name": item.find("name").attrib.get("value", "") if item.find("name") is not None else "",
        "image": item.find("thumbnail").attrib.get("value", None) if item.find("thumbnail") is not None else None,
        "bgg_url": f"https://boardgamegeek.com/boardgamedesigner/{item.attrib['id']}",
        "last_modified": datetime.utcnow(),
    }

async def fetch_bgg_hotness_persons() -> List[Dict[str, Any]]:
    log_info("👤 Rozpoczynam pobieranie Hotness Persons z BGG")
    persons: List[Dict[str, Any]] = []
    try:
        async with _make_client() as client:
            root = await fetch_xml(client, HOT_PERSONS_URL)
            items = root.findall("item")

            for idx, item in enumerate(items, start=1):
                person = extract_hot_person(item)
                log_info(f"[{idx}/{len(items)}] 👤 Rank {person['rank']} - {person['name']}")
                persons.append(person)

            log_success(f"👤 Zakończono przetwarzanie {len(persons)} hotness osób")
            return persons

    except httpx.HTTPError as e:
        log_error(f"❌ HTTP error while fetching hot persons: {e}")
    except ET.ParseError as e:
        log_error(f"❌ XML parsing error for hot persons: {e}")
    except Exception as e:
        log_error(f"❌ Unexpected error while parsing hot persons: {e}")
    return []
