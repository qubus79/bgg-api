# scraper/scraper_bgg_hotness.py

import xmltodict
import requests
from datetime import datetime
from app.models.bgg_hotness_game import BGGHotnessGame
from app.models.bgg_hotness_person import BGGHotnessPerson
from app.database import AsyncSessionLocal
from sqlalchemy import delete
from app.utils.logging import log_info, log_success

# ----------------------------
# 🟣 HOTNESS GAMES SCRAPER
# ----------------------------

async def fetch_bgg_hotness_games():
    url = 'https://boardgamegeek.com/xmlapi2/hot?type=boardgame'
    log_info("Fetching BGG Hotness - Games...")
    response = requests.get(url)
    response.raise_for_status()

    data = xmltodict.parse(response.content)
    items = data.get("items", {}).get("item", [])

    async with AsyncSessionLocal() as session:
        await session.execute(delete(BGGHotnessGame))
        for item in items:
            game = BGGHotnessGame(
                bgg_id=int(item["@id"]),
                rank=int(item["@rank"]),
                name=item["name"]["@value"],
                year_published=int(item.get("yearpublished", {}).get("@value", 0)),
                image=item.get("thumbnail", {}).get("@value", None),
                last_modified=datetime.utcnow(),
            )
            session.add(game)
        await session.commit()
        log_success(f"🎲 Synced {len(items)} hotness games from BGG")


# ----------------------------
# 🟡 HOTNESS PERSONS SCRAPER
# ----------------------------

async def fetch_bgg_hotness_persons():
    url = 'https://boardgamegeek.com/xmlapi2/hot?type=boardgameperson'
    log_info("Fetching BGG Hotness - Persons...")
    response = requests.get(url)
    response.raise_for_status()

    data = xmltodict.parse(response.content)
    items = data.get("items", {}).get("item", [])

    async with AsyncSessionLocal() as session:
        await session.execute(delete(BGGHotnessPerson))
        for item in items:
            person = BGGHotnessPerson(
                bgg_id=int(item["@id"]),
                rank=int(item["@rank"]),
                name=item["name"]["@value"],
                image=item.get("thumbnail", {}).get("@value", None),
                last_modified=datetime.utcnow(),
            )
            session.add(person)
        await session.commit()
        log_success(f"👤 Synced {len(items)} hotness persons from BGG")
