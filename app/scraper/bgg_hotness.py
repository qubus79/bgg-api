# app/scraper/bgg_hotness.py

import asyncio
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any
from app.database import AsyncSessionLocal
from app.models.bgg_hotness import BGGHotGame, BGGHotPerson
from sqlalchemy import select
from app.utils.logging import log_info, log_success, log_error, log_warning
import time

HOT_GAMES_URL = "https://boardgamegeek.com/xmlapi2/hot?type=boardgame"
HOT_PERSONS_URL = "https://boardgamegeek.com/xmlapi2/hot?type=boardgameperson"
THING_URL = "https://boardgamegeek.com/xmlapi2/thing?id={bgg_id}&stats=1"

# ----------------------------
# 🌐 XML Fetching with Retry
# ----------------------------
async def fetch_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    log_info(f"➡️ Fetching XML from: {url}")
    resp = await client.get(url)
    while resp.status_code == 202:
        log_info("⏳ Czekam na przetworzenie danych przez BGG...")
        await asyncio.sleep(2)
        resp = await client.get(url)
    resp.raise_for_status()
    return ET.fromstring(resp.text)

async def retry_with_backoff(fetch_func, retries=5, initial_delay=2, max_delay=60):
    delay = initial_delay
    for attempt in range(retries):
        try:
            return await fetch_func()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                log_warning(f"🔁 Too Many Requests (429). Retry {attempt+1}/{retries} after {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
            else:
                raise
    raise RuntimeError("❌ Exceeded maximum retry attempts due to repeated 429 errors")

# ----------------------------
# 🟣 HOTNESS GAMES
# ----------------------------
def extract_hot_game(item: ET.Element) -> Dict[str, Any]:
    return {
        "bgg_id": int(item.attrib["id"]),
        "rank": int(item.attrib.get("rank", 0)),
        "name": item.find("name").attrib.get("value", "") if item.find("name") is not None else "",
        "year_published": int(item.find("yearpublished").attrib.get("value", 0)) if item.find("yearpublished") is not None else None,
        "image": item.find("thumbnail").attrib.get("value", None) if item.find("thumbnail") is not None else None,
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
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            root = await fetch_xml(client, HOT_GAMES_URL)
            items = root.findall("item")
            games = []

            for idx, item in enumerate(items, start=1):
                game = extract_hot_game(item)
                bgg_id = game["bgg_id"]
                detail_url = THING_URL.format(bgg_id=bgg_id)
                log_info(f"[{idx}/{len(items)}] 🔥 Rank {game['rank']} - {game['name']}")
                detail_root = await retry_with_backoff(lambda: fetch_xml(client, detail_url))
                detail_item = detail_root.find("item")
                if detail_item:
                    details = extract_hot_game_details(detail_item)
                    game.update(details)
                games.append(game)
                time.sleep(2)

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
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            root = await fetch_xml(client, HOT_PERSONS_URL)
            items = root.findall("item")
            persons = []

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

