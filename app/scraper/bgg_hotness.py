import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from app.database import AsyncSessionLocal
from app.models.bgg_hotness import BGGHotGame
from app.models.bgg_hotness import BGGHotPerson
from sqlalchemy import delete
from app.utils.logging import log_info, log_success

async def fetch_xml(url: str) -> ET.Element:
    async with httpx.AsyncClient() as client:
        log_info(f"➡️ Fetching XML from: {url}")
        resp = await client.get(url)
        resp.raise_for_status()
        return ET.fromstring(resp.text)

# ----------------------------
# 🟣 HOTNESS GAMES SCRAPER
# ----------------------------
async def fetch_bgg_hotness_games():
    url = "https://boardgamegeek.com/xmlapi2/hot?type=boardgame"
    root = await fetch_xml(url)

    items = root.findall("item")

    async with AsyncSessionLocal() as session:
        await session.execute(delete(BGGHotGame))
        for item in items:
            session.add(BGGHotGame(
                bgg_id=int(item.attrib["id"]),
                rank=int(item.attrib["rank"]),
                name=item.find("name").attrib["value"],
                year_published=int(item.find("yearpublished").attrib.get("value", 0)) if item.find("yearpublished") is not None else None,
                image=item.find("thumbnail").attrib.get("value", None) if item.find("thumbnail") is not None else None,
                last_modified=datetime.utcnow(),
            ))
        await session.commit()
        log_success(f"🎲 Synced {len(items)} hotness games from BGG")

# ----------------------------
# 🟡 HOTNESS PERSONS SCRAPER
# ----------------------------
async def fetch_bgg_hotness_persons():
    url = "https://boardgamegeek.com/xmlapi2/hot?type=boardgameperson"
    root = await fetch_xml(url)

    items = root.findall("item")

    async with AsyncSessionLocal() as session:
        await session.execute(delete(BGGHotPerson))
        for item in items:
            session.add(BGGHotPerson(
                bgg_id=int(item.attrib["id"]),
                rank=int(item.attrib["rank"]),
                name=item.find("name").attrib["value"],
                image=item.find("thumbnail").attrib.get("value", None) if item.find("thumbnail") is not None else None,
                last_modified=datetime.utcnow(),
            ))
        await session.commit()
        log_success(f"👤 Synced {len(items)} hotness persons from BGG")
