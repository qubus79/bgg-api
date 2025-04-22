# app/scraper_bgg.py

import httpx
import xml.etree.ElementTree as ET
from typing import List
from app.config import settings
import asyncio


async def fetch_bgg_collection(username: str) -> List[dict]:
    url = f"https://boardgamegeek.com/xmlapi2/collection?username={username}&stats=1"
    async with httpx.AsyncClient() as client:
        print(f"➡️ Fetching BGG collection for user: {username}...")
        resp = await client.get(url)
        while resp.status_code == 202:
            print("⌛ Waiting for BGG to prepare the collection data...")
            await asyncio.sleep(2)
            resp = await client.get(url)
        resp.raise_for_status()

    games = []
    root = ET.fromstring(resp.text)

    for item in root.findall("item"):
        game_id = int(item.attrib["objectid"])
        subtype = item.attrib.get("subtype")

        name = item.findtext("name")
        year_published = item.findtext("yearpublished")
        image = item.findtext("image")
        thumbnail = item.findtext("thumbnail")

        status = item.find("status")
        own = status.attrib.get("own") == "1"
        prev_owned = status.attrib.get("prevowned") == "1"
        want = status.attrib.get("want") == "1"
        want_to_play = status.attrib.get("wanttoplay") == "1"
        want_to_buy = status.attrib.get("wanttobuy") == "1"
        wishlist = status.attrib.get("wishlist") == "1"
        preordered = status.attrib.get("preordered") == "1"

        games.append({
            "game_id": game_id,
            "name": name,
            "year_published": year_published,
            "image": image,
            "thumbnail": thumbnail,
            "subtype": subtype,
            "own": own,
            "prev_owned": prev_owned,
            "want": want,
            "want_to_play": want_to_play,
            "want_to_buy": want_to_buy,
            "wishlist": wishlist,
            "preordered": preordered
        })

    print(f"✅ Parsed {len(games)} games from BGG collection")
    return games
