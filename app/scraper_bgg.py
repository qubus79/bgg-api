import httpx
import xml.etree.ElementTree as ET
from typing import Dict, Any
import asyncio
import time
from app.database import AsyncSessionLocal
from app.models import BGGGame
from sqlalchemy import select
from app.utils import log_info, log_success


async def fetch_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    log_info(f"➡️ Fetching XML from: {url}")
    resp = await client.get(url)
    while resp.status_code == 202:
        log_info("⏳ Waiting for BGG API to be ready...")
        await asyncio.sleep(2)
        resp = await client.get(url)
    resp.raise_for_status()
    return ET.fromstring(resp.text)


def parse_collection_data(root: ET.Element) -> Dict[str, ET.Element]:
    return {item.attrib['objectid']: item for item in root.findall("item")}


def extract_collection_basics(item: ET.Element) -> Dict[str, Any]:
    return {
        "title": item.findtext("name"),
        "year_published": int(item.findtext("yearpublished") or 0),
        "image": item.findtext("image"),
        "thumbnail": item.findtext("thumbnail"),
        "num_plays": int(item.findtext("numplays") or 0),
        "my_rating": float(stats_el.attrib.get("value", 0)) if stats_el is not None else None,
        "average_rating": float(stats_el.find("average").attrib.get("value", 0)) if stats_el is not None and stats_el.find("average") is not None else None,
        "bgg_rank": int(bgg_rank) if bgg_rank and bgg_rank.isdigit() else None,
        "min_players": int(detail_item.attrib.get("minplayers", 0)),
        "max_players": int(detail_item.attrib.get("maxplayers", 0)),
        "min_playtime": int(detail_item.attrib.get("minplaytime", 0)),
        "max_playtime": int(detail_item.attrib.get("maxplaytime", 0)),
        "play_time": int(detail_item.attrib.get("playingtime", 0)),
        "status_owned": item.find("status").attrib.get("own") == "1" if item.find("status") is not None else False,
        "status_preordered": item.find("status").attrib.get("preordered") == "1" if item.find("status") is not None else False,
        "status_wishlist": item.find("status").attrib.get("wishlist") == "1" if item.find("status") is not None else False,
        "status_fortrade": item.find("status").attrib.get("fortrade") == "1" if item.find("status") is not None else False,
        "status_prevowned": item.find("status").attrib.get("prevowned") == "1" if item.find("status") is not None else False,
        "status_wanttoplay": item.find("status").attrib.get("wanttoplay") == "1" if item.find("status") is not None else False,
        "status_wanttobuy": item.find("status").attrib.get("wanttobuy") == "1" if item.find("status") is not None else False,
        "status_wishlist_priority": int(item.find("status").attrib.get("wishlistpriority") or 0) if item.find("status") is not None else None,
    }


def extract_details(detail_item: ET.Element) -> Dict[str, Any]:
    stats_el = detail_item.find("statistics/rating")
    ranks = stats_el.find("ranks") if stats_el is not None else None
    bgg_rank = None
    if ranks is not None:
        for rank in ranks.findall("rank"):
            if rank.attrib.get("friendlyname") == "Board Game Rank":
                bgg_rank = rank.attrib.get("value")
                break

    name = None
    for name_el in detail_item.findall("name"):
        if name_el.attrib.get("type") == "primary":
            name = name_el.attrib.get("value")
            break

    links = detail_item.findall("link")
    return {
        "original_title": name,
        "description": detail_item.findtext("description"),
        "mechanics": [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgamemechanic"],
        "designers": [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgamedesigner"],
        "artists": [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgameartist"],
        # "my_rating": float(stats_el.attrib.get("value", 0)) if stats_el is not None else None,
        # "average_rating": float(stats_el.find("average").attrib.get("value", 0)) if stats_el is not None and stats_el.find("average") is not None else None,
        # "bgg_rank": int(bgg_rank) if bgg_rank and bgg_rank.isdigit() else None,
        # "min_players": int(detail_item.attrib.get("minplayers", 0)),
        # "max_players": int(detail_item.attrib.get("maxplayers", 0)),
        # "min_playtime": int(detail_item.attrib.get("minplaytime", 0)),
        # "max_playtime": int(detail_item.attrib.get("maxplaytime", 0)),
        # "play_time": int(detail_item.attrib.get("playingtime", 0)),
        "min_age": int(detail_item.attrib.get("minage", 0)),
        "type": detail_item.attrib.get("type", None)
    }


async def fetch_bgg_collection(username: str) -> None:
    log_info("📥 Rozpoczynam pobieranie kolekcji BGG")
    collection_url = f"https://boardgamegeek.com/xmlapi2/collection?username={username}&stats=1"
    thing_url = "https://boardgamegeek.com/xmlapi2/thing?id={bgg_id}&stats=1"

    async with httpx.AsyncClient() as client:
        collection_root = await fetch_xml(client, collection_url)
        collection_data = parse_collection_data(collection_root)

        log_info(f"🔍 Znaleziono {len(collection_data)} gier w kolekcji")

        for idx, (bgg_id, item) in enumerate(collection_data.items(), start=1):
            log_info(f"\n[{idx}/{len(collection_data)}] 🧩 Przetwarzam grę ID={bgg_id}...")

            basic_data = extract_collection_basics(item)
            detail_url = thing_url.format(bgg_id=bgg_id)
            detail_root = await fetch_xml(client, detail_url)
            detail_item = detail_root.find("item")
            if not detail_item:
                log_info(f"⚠️ Pominięto grę {bgg_id} - brak danych szczegółowych")
                continue

            detailed_data = extract_details(detail_item)
            full_data = {
                "bgg_id": int(bgg_id),
                **basic_data,
                **detailed_data,
            }

            async with AsyncSessionLocal() as session:
                result = await session.execute(select(BGGGame).where(BGGGame.bgg_id == int(bgg_id)))
                existing = result.scalars().first()

                if existing:
                    for field, value in full_data.items():
                        setattr(existing, field, value)
                    log_info(f"♻️ Zaktualizowano dane gry ID={bgg_id}")
                else:
                    session.add(BGGGame(**full_data))
                    log_info(f"➕ Dodano nową grę ID={bgg_id}")

                await session.commit()

            log_info("⏳ Pauza 5 sekund by uniknąć limitów BGG")
            time.sleep(5)

    log_success("🎉 Zakończono przetwarzanie całej kolekcji BGG")
