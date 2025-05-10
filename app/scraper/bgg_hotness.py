# app/scraper/bgg_hotness.py

import asyncio
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any
from app.database import AsyncSessionLocal
from app.models.bgg_hotness import BGGHotGame, BGGHotPerson
from sqlalchemy import delete
from app.utils.logging import log_info, log_success, log_error, log_warning

HOT_GAMES_URL = "https://boardgamegeek.com/xmlapi2/hot?type=boardgame"
HOT_PERSONS_URL = "https://boardgamegeek.com/xmlapi2/hot?type=boardgameperson"

# ----------------------------
# 🌐 XML Fetching
# ----------------------------

async def fetch_xml(url: str) -> ET.Element:
    log_info(f"➡️ Fetching XML from: {url}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        while response.status_code == 202:
            log_info("⏳ Czekam na przetworzenie danych przez BGG...")
            await asyncio.sleep(2)
            response = await client.get(url)
        response.raise_for_status()
        return ET.fromstring(response.text)

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
        "last_modified": datetime.utcnow(),
    }

async def fetch_bgg_hotness_games() -> List[Dict[str, Any]]:
    log_info("🎲 Rozpoczynam pobieranie Hotness Games z BGG")
    try:
        root = await fetch_xml(HOT_GAMES_URL)
        items = root.findall("item")
        games = []

        for idx, item in enumerate(items, start=1):
            game = extract_hot_game(item)
            log_info(f"[{idx}/{len(items)}] 🔥 Rank {game['rank']} - {game['name']}")
            games.append(game)

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
        "name": item.findtext("name") or "",
        "image": item.findtext("thumbnail") or "",
        "last_modified": datetime.utcnow(),
    }

async def fetch_bgg_hotness_persons() -> List[Dict[str, Any]]:
    log_info("👤 Rozpoczynam pobieranie Hotness Persons z BGG")
    try:
        root = await fetch_xml(HOT_PERSONS_URL)
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
