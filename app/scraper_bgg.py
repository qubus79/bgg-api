import httpx
import xml.etree.ElementTree as ET
from typing import List, Dict, Any
import asyncio
import logging
import random

logger = logging.getLogger(__name__)


async def fetch_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    for attempt in range(5):
        try:
            resp = await client.get(url)
            while resp.status_code == 202:
                await asyncio.sleep(2)
                resp = await client.get(url)
            resp.raise_for_status()
            return ET.fromstring(resp.text)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning(f"Got 429 Too Many Requests. Retrying after delay...")
                await asyncio.sleep(3)
            else:
                raise e
    raise Exception(f"Failed to fetch XML after retries: {url}")


def parse_status(element: ET.Element) -> Dict[str, Any]:
    attrs = element.attrib
    return {
        "own": attrs.get("own") == "1",
        "prevowned": attrs.get("prevowned") == "1",
        "preordered": attrs.get("preordered") == "1",
        "want": attrs.get("want") == "1",
        "wanttoplay": attrs.get("wanttoplay") == "1",
        "wanttobuy": attrs.get("wanttobuy") == "1",
        "wishlist": attrs.get("wishlist") == "1",
        "fortrade": attrs.get("fortrade") == "1",
        "wishlist_priority": int(attrs.get("wishlistpriority", 0))
    }


def parse_int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def parse_float(value: str | None) -> float | None:
    try:
        return float(value) if value and value != "N/A" else None
    except ValueError:
        return None


def get_detail_field(item: ET.Element, path: str, attr: str = "value") -> str | None:
    node = item.find(path)
    return node.attrib.get(attr) if node is not None and attr in node.attrib else None


def get_rank_value(ranks: ET.Element) -> int | None:
    for rank in ranks.findall("rank"):
        if rank.attrib.get("friendlyname") == "Board Game Rank" and rank.attrib.get("value", "") != "Not Ranked":
            return parse_int(rank.attrib.get("value"))
    return None


def extract_links(detail_item: ET.Element, link_type: str) -> List[str]:
    return [link.attrib.get("value") for link in detail_item.findall("link") if link.attrib.get("type") == link_type]


async def fetch_bgg_collection(username: str) -> List[Dict[str, Any]]:
    url = f"https://boardgamegeek.com/xmlapi2/collection?username={username}&stats=1"
    detail_base = "https://boardgamegeek.com/xmlapi2/thing?id={game_id}&stats=1"

    games = []
    async with httpx.AsyncClient() as client:
        logger.info(f"➡️ Fetching BGG collection for user: {username}...")
        collection_root = await fetch_xml(client, url)
        for item in collection_root.findall("item"):
            game_id = int(item.attrib["objectid"])
            name = item.findtext("name")
            year_published = parse_int(item.findtext("yearpublished"))
            image = item.findtext("image")
            thumbnail = item.findtext("thumbnail")
            num_plays = parse_int(item.findtext("numplays")) or 0

            status = parse_status(item.find("status"))

            detail_url = detail_base.format(game_id=game_id)
            await asyncio.sleep(random.uniform(1.1, 2.0))
            detail_root = await fetch_xml(client, detail_url)
            detail_item = detail_root.find("item")

            if detail_item is None:
                logger.warning(f"No details found for game {game_id}")
                continue

            stats = detail_item.find("statistics/rating")
            my_rating = parse_float(stats.attrib.get("value")) if stats is not None else None
            avg_rating = parse_float(get_detail_field(stats, "average")) if stats is not None else None
            bgg_rank = get_rank_value(stats.find("ranks")) if stats is not None else None

            min_players = parse_int(detail_item.attrib.get("minplayers"))
            max_players = parse_int(detail_item.attrib.get("maxplayers"))
            min_playtime = parse_int(detail_item.attrib.get("minplaytime"))
            max_playtime = parse_int(detail_item.attrib.get("maxplaytime"))
            play_time = parse_int(detail_item.attrib.get("playingtime"))
            min_age = parse_int(detail_item.attrib.get("minage"))

            description = detail_item.findtext("description")
            original_name = next((n.attrib.get("value") for n in detail_item.findall("name") if n.attrib.get("type") == "primary"), None)

            mechanics = extract_links(detail_item, "boardgamemechanic")
            designers = extract_links(detail_item, "boardgamedesigner")
            artists = extract_links(detail_item, "boardgameartist")

            games.append({
                "game_id": game_id,
                "name": name,
                "original_name": original_name,
                "year_published": year_published,
                "image": image,
                "thumbnail": thumbnail,
                "num_plays": num_plays,
                "status": status,
                "stats": {
                    "my_rating": my_rating,
                    "average_rating": avg_rating,
                    "bgg_rank": bgg_rank,
                    "min_players": min_players,
                    "max_players": max_players,
                    "min_playtime": min_playtime,
                    "max_playtime": max_playtime,
                    "play_time": play_time,
                    "min_age": min_age
                },
                "description": description,
                "mechanics": mechanics,
                "designers": designers,
                "artists": artists
            })

            await asyncio.sleep(0.5)  # ⚠️ Sleep to avoid 429s

    logger.info(f"✅ Parsed {len(games)} games from BGG")
    return games
