# app/scraper/bgg_hotness.py

import asyncio
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any
from app.database import AsyncSessionLocal
from app.models.bgg_hotness import BGGHotGame
from sqlalchemy import delete, select
from app.utils.logging import log_info, log_success, log_error

HOT_GAMES_URL = "https://boardgamegeek.com/xmlapi2/hot?type=boardgame"
THING_URL_TEMPLATE = "https://boardgamegeek.com/xmlapi2/thing?id={bgg_id}&stats=1"

# ----------------------------
# 🌐 XML Fetching
# ----------------------------

async def fetch_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    log_info(f"➡️ Fetching XML from: {url}")
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

def extract_basic_game(item: ET.Element) -> Dict[str, Any]:
    return {
        "bgg_id": int(item.attrib["id"]),
        "rank": int(item.attrib.get("rank", 0)),
        "name": item.find("name").attrib.get("value", "") if item.find("name") is not None else "",
        "year_published": int(item.find("yearpublished").attrib.get("value", 0)) if item.find("yearpublished") is not None else None,
        "image": item.find("thumbnail").attrib.get("value", None) if item.find("thumbnail") is not None else None,
        "bgg_url": f"https://boardgamegeek.com/boardgame/{item.attrib['id']}",
        "last_modified": datetime.utcnow(),
    }

def extract_game_details(detail_item: ET.Element) -> Dict[str, Any]:
    links = detail_item.findall("link")
    stats_el = detail_item.find("statistics/ratings")

    average_weight = None
    if stats_el is not None and stats_el.find("averageweight") is not None:
        try:
            average_weight = float(stats_el.find("averageweight").attrib.get("value"))
        except (ValueError, TypeError):
            average_weight = None

    return {
        "description": detail_item.findtext("description"),
        "mechanics": [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgamemechanic"],
        "designers": [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgamedesigner"],
        "artists": [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgameartist"],
        "min_players": int(detail_item.find("minplayers").attrib.get("value", 0)) if detail_item.find("minplayers") is not None else None,
        "max_players": int(detail_item.find("maxplayers").attrib.get("value", 0)) if detail_item.find("maxplayers") is not None else None,
        "min_playtime": int(detail_item.find("minplaytime").attrib.get("value", 0)) if detail_item.find("minplaytime") is not None else None,
        "max_playtime": int(detail_item.find("maxplaytime").attrib.get("value", 0)) if detail_item.find("maxplaytime") is not None else None,
        "play_time": int(detail_item.find("playingtime").attrib.get("value", 0)) if detail_item.find("playingtime") is not None else None,
        "min_age": int(detail_item.find("minage").attrib.get("value", 0)) if detail_item.find("minage") is not None else None,
        "type": detail_item.attrib.get("type", None),
        "weight": average_weight,
        "average_rating": float(stats_el.find("average").attrib.get("value", 0)) if stats_el is not None and stats_el.find("average") is not None else None,
        "bgg_rank": int(stats_el.find("ranks/rank").attrib.get("value")) if stats_el is not None and stats_el.find("ranks/rank") is not None and stats_el.find("ranks/rank").attrib.get("value").isdigit() else None,
    }

async def fetch_bgg_hotness_games() -> List[Dict[str, Any]]:
    log_info("🎲 Rozpoczynam pobieranie Hotness Games z BGG")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            root = await fetch_xml(client, HOT_GAMES_URL)
            items = root.findall("item")
            games: List[Dict[str, Any]] = []

            for idx, item in enumerate(items, start=1):
                basic = extract_basic_game(item)
                log_info(f"[{idx}/{len(items)}] 🔥 Rank {basic['rank']} - {basic['name']}")

                detail_url = THING_URL_TEMPLATE.format(bgg_id=basic["bgg_id"])
                detail_root = await fetch_xml(client, detail_url)
                detail_item = detail_root.find("item")
                if not detail_item:
                    log_info(f"⚠️ Pominięto grę {basic['name']} - brak danych szczegółowych")
                    continue

                details = extract_game_details(detail_item)
                full_game = {**basic, **details}
                games.append(full_game)

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
        "bgg_url": f"https://boardgamegeek.com/boardgamedesigner/{item.attrib['id']}"
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
