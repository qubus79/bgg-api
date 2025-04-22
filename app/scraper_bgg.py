# app/scraper_bgg.py

import httpx
import xml.etree.ElementTree as ET
from typing import List, Dict, Any
import asyncio

def parse_xml_element(element: ET.Element) -> Dict[str, Any]:
    return {k: v for k, v in element.attrib.items()}

def extract_text(element: ET.Element) -> str:
    return element.text.strip() if element is not None and element.text else ""

async def fetch_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    resp = await client.get(url)
    while resp.status_code == 202:
        await asyncio.sleep(2)
        resp = await client.get(url)
    resp.raise_for_status()
    return ET.fromstring(resp.text)

async def fetch_bgg_collection(username: str) -> List[dict]:
    url = f"https://boardgamegeek.com/xmlapi2/collection?username={username}&stats=1"
    detail_base = "https://boardgamegeek.com/xmlapi2/thing?id={game_id}&stats=1"

    async with httpx.AsyncClient() as client:
        print(f"➡️ Fetching BGG collection for user: {username}...")
        collection_root = await fetch_xml(client, url)

        items = collection_root.findall("item")
        games = []

        for item in items:
            game_id = int(item.attrib["objectid"])
            name = item.find("name").text if item.find("name") is not None else None
            year_published = item.findtext("yearpublished")
            image = item.findtext("image")
            thumbnail = item.findtext("thumbnail")
            num_plays = item.findtext("numplays")

            status_el = item.find("status")
            statuses = status_el.attrib if status_el is not None else {}

            detail_url = detail_base.format(game_id=game_id)
            detail_root = await fetch_xml(client, detail_url)
            detail_item = detail_root.find("item")

            stats_el = detail_item.find("statistics/rating") if detail_item is not None else None
            avg_rating = stats_el.find("average").attrib.get("value") if stats_el is not None else None
            my_rating = stats_el.attrib.get("value") if stats_el is not None else None
            ranks = stats_el.find("ranks") if stats_el is not None else None
            bgg_rank = None
            if ranks is not None:
                for rank in ranks.findall("rank"):
                    if rank.attrib.get("friendlyname") == "Board Game Rank":
                        bgg_rank = rank.attrib.get("value")
                        break

            min_players = detail_item.attrib.get("minplayers") if detail_item is not None else None
            max_players = detail_item.attrib.get("maxplayers") if detail_item is not None else None
            min_playtime = detail_item.attrib.get("minplaytime") if detail_item is not None else None
            max_playtime = detail_item.attrib.get("maxplaytime") if detail_item is not None else None
            playtime = detail_item.attrib.get("playingtime") if detail_item is not None else None
            min_age = detail_item.attrib.get("minage") if detail_item is not None else None

            description = detail_item.findtext("description")
            original_name = None
            for name_el in detail_item.findall("name"):
                if name_el.attrib.get("type") == "primary":
                    original_name = name_el.attrib.get("value")

            links = detail_item.findall("link")
            mechanics = [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgamemechanic"]
            designers = [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgamedesigner"]
            artists = [l.attrib["value"] for l in links if l.attrib.get("type") == "boardgameartist"]

            games.append({
                "game_id": game_id,
                "name": name,
                "original_name": original_name,
                "year_published": int(year_published) if year_published and year_published.isdigit() else None,
                "image": image,
                "thumbnail": thumbnail,
                "num_plays": int(num_plays) if num_plays and num_plays.isdigit() else 0,
                "status": {
                    "own": statuses.get("own") == "1",
                    "prevowned": statuses.get("prevowned") == "1",
                    "preordered": statuses.get("preordered") == "1",
                    "want": statuses.get("want") == "1",
                    "wanttoplay": statuses.get("wanttoplay") == "1",
                    "wanttobuy": statuses.get("wanttobuy") == "1",
                    "wishlist": statuses.get("wishlist") == "1",
                    "fortrade": statuses.get("fortrade") == "1",
                    "wishlist_priority": int(statuses.get("wishlistpriority")) if statuses.get("wishlistpriority") and statuses.get("wishlistpriority").isdigit() else None
                },
                "stats": {
                    "my_rating": float(my_rating) if my_rating and my_rating != "N/A" else None,
                    "average_rating": float(avg_rating) if avg_rating and avg_rating != "N/A" else None,
                    "bgg_rank": int(bgg_rank) if bgg_rank and bgg_rank.isdigit() else None,
                    "min_players": int(min_players) if min_players and min_players.isdigit() else None,
                    "max_players": int(max_players) if max_players and max_players.isdigit() else None,
                    "min_playtime": int(min_playtime) if min_playtime and min_playtime.isdigit() else None,
                    "max_playtime": int(max_playtime) if max_playtime and max_playtime.isdigit() else None,
                    "play_time": int(playtime) if playtime and playtime.isdigit() else None,
                    "min_age": int(min_age) if min_age and min_age.isdigit() else None
                },
                "description": description,
                "mechanics": mechanics,
                "designers": designers,
                "artists": artists
            })

        print(f"✅ Parsed {len(games)} games with details")
        return games
