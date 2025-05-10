# app/scraper/bgg_accessory_scraper.py

import httpx
import xml.etree.ElementTree as ET
from typing import Dict, Any
import asyncio
import time
from app.database import AsyncSessionLocal
from app.models.bgg_accessory import BGGAccessory
from sqlalchemy import select
from app.utils.logging import log_info, log_success

def get_bool(attr: str | None) -> bool:
    return attr == "1"

def get_float(attr: str | None) -> float:
    try:
        return float(attr)
    except (TypeError, ValueError):
        return 0.0

def get_int(attr: str | None) -> int:
    try:
        return int(attr)
    except (TypeError, ValueError):
        return 0

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
    status = item.find("status")
    return {
        "name": item.findtext("name"),
        "year_published": get_int(item.findtext("yearpublished")),
        "image": item.findtext("image"),
        "num_plays": get_int(item.findtext("numplays")),
        "my_rating": get_float(item.find("stats/rating").attrib.get("value") if item.find("stats/rating") is not None else None),
        "average_rating": get_float(item.find("stats/rating/average").attrib.get("value") if item.find("stats/rating/average") is not None else None),
        "bgg_rank": get_int(item.find("stats/rating/ranks/rank").attrib.get("value") if item.find("stats/rating/ranks/rank") is not None else None),
        "owned": get_bool(status.attrib.get("own") if status is not None else None),
        "preordered": get_bool(status.attrib.get("preordered") if status is not None else None),
        "wishlist": get_bool(status.attrib.get("wishlist") if status is not None else None),
        "want_to_buy": get_bool(status.attrib.get("wanttobuy") if status is not None else None),
        "want_to_play": get_bool(status.attrib.get("wanttoplay") if status is not None else None),
        "want": get_bool(status.attrib.get("want") if status is not None else None),
        "for_trade": get_bool(status.attrib.get("fortrade") if status is not None else None),
        "previously_owned": get_bool(status.attrib.get("prevowned") if status is not None else None),
        "last_modified": status.attrib.get("lastmodified") if status is not None else None
    }

def extract_details(detail_item: ET.Element) -> Dict[str, Any]:
    publisher_links = [l.attrib.get("value") for l in detail_item.findall("link") if l.attrib.get("type") == "boardgamepublisher"]
    publisher_str = ", ".join(publisher_links)
    return {
        "description": detail_item.findtext("description"),
        "publisher": publisher_str,
    }

async def fetch_bgg_accessories(username: str) -> None:
    log_info("📅 Rozpoczynam pobieranie akcesorii BGG")
    collection_url = f"https://boardgamegeek.com/xmlapi2/collection?username={username}&subtype=boardgameaccessory&stats=1"
    thing_url = "https://boardgamegeek.com/xmlapi2/thing?id={bgg_id}&stats=1"

    async with httpx.AsyncClient() as client:
        collection_root = await fetch_xml(client, collection_url)
        collection_data = parse_collection_data(collection_root)

        log_info(f"🔍 Znaleziono {len(collection_data)} akcesorii")

        for idx, (bgg_id, item) in enumerate(collection_data.items(), start=1):
            basic_data = extract_collection_basics(item)
            title = basic_data.get("name") or f"ID={bgg_id}"
            log_info(f"[{idx}/{len(collection_data)}] 🧰 Przetwarzam akcesorium: {title} (ID={bgg_id})")

            detail_url = thing_url.format(bgg_id=bgg_id)
            detail_root = await fetch_xml(client, detail_url)
            detail_item = detail_root.find("item")
            if not detail_item:
                log_info(f"⚠️ Pominięto {title} (ID={bgg_id}) - brak danych szczegółowych")
                continue

            detailed_data = extract_details(detail_item)
            full_data = {
                "bgg_id": int(bgg_id),
                **basic_data,
                **detailed_data,
            }

            async with AsyncSessionLocal() as session:
                result = await session.execute(select(BGGAccessory).where(BGGAccessory.bgg_id == int(bgg_id)))
                existing = result.scalars().first()

                if existing:
                    for field, value in full_data.items():
                        setattr(existing, field, value)
                    log_info(f"♻️ Zaktualizowano dane akcesorium: {title}")
                else:
                    session.add(BGGAccessory(**full_data))
                    log_info(f"➕ Dodano nowe akcesorium: {title}")

                await session.commit()

            time.sleep(2)

    # Usuwanie nieistniejących
    current_ids = set(int(bgg_id) for bgg_id in collection_data.keys())
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGAccessory.bgg_id))
        all_db_ids = set(result.scalars().all())
        to_delete = all_db_ids - current_ids
        if to_delete:
            await session.execute(BGGAccessory.__table__.delete().where(BGGAccessory.bgg_id.in_(to_delete)))
            await session.commit()
            log_info(f"🗑 Usunięto {len(to_delete)} nieistniejących akcesorii")

    log_success("🎉 Zakończono przetwarzanie kolekcji akcesorii BGG")
