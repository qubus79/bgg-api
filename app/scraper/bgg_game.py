import os
import httpx
import xml.etree.ElementTree as ET
from typing import Dict, Any
import asyncio
from app.database import AsyncSessionLocal
from app.models.bgg_game import BGGGame
from sqlalchemy import select
from app.utils.logging import log_info, log_success

BGG_XML_BASE = "https://boardgamegeek.com/xmlapi2"
BGG_API_TOKEN = os.getenv("BGG_API_TOKEN")  # ustaw w .env / docker-compose
USER_AGENT = "BoardGamesApp/1.0 (+contact: your-email@example.com)"

def _default_headers() -> Dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if BGG_API_TOKEN:
        headers["Authorization"] = f"Bearer {BGG_API_TOKEN}"
    return headers

def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_default_headers(),
        follow_redirects=True,
        http2=True,
        timeout=httpx.Timeout(30.0),
    )

async def fetch_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    """
    Pobierz XML z obsługą:
    - 202 Accepted (kolejka na BGG) + Retry-After,
    - 429 Too Many Requests + Retry-After,
    - 5xx z backoffem,
    - 401/403 (problem z tokenem).
    """
    log_info(f"➡️ Fetching XML from: {url}")

    base_delay = 1.0
    max_attempts = 12

    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = await client.get(url)

            # Sukces
            if resp.status_code == 200:
                return ET.fromstring(resp.text)

            # 202 — zapytanie w kolejce
            if resp.status_code == 202:
                delay_hdr = resp.headers.get("Retry-After")
                sleep_s = float(delay_hdr) if delay_hdr else base_delay * attempt
                log_info(f"⏳ 202 Accepted — czekam {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_s)
                continue

            # 429 — za dużo zapytań
            if resp.status_code == 429:
                delay_hdr = resp.headers.get("Retry-After")
                sleep_s = float(delay_hdr) if delay_hdr else base_delay * attempt
                log_info(f"🚦 429 Too Many Requests — czekam {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_s)
                continue

            # 5xx — spróbuj ponownie z backoffem
            if resp.status_code in (500, 502, 503, 504):
                sleep_s = base_delay * attempt
                log_info(f"🛠 {resp.status_code} — retry za {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_s)
                continue

            # 401/403 — token nie ustawiony/niepoprawny/niezatwierdzona aplikacja
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"BGG auth error {resp.status_code}. "
                    "Sprawdź BGG_API_TOKEN i czy aplikacja na BGG jest zatwierdzona."
                )

            # Inne kody — przerwij standardowym wyjątkiem
            resp.raise_for_status()

        except Exception as e:
            last_exc = e
            sleep_s = base_delay * attempt
            log_info(f"⚠️ Wyjątek {type(e).__name__}: {e} — retry za {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
            await asyncio.sleep(sleep_s)

    # Po próbach — rzuć ostatni wyjątek
    if last_exc:
        raise last_exc
    raise RuntimeError("Niepowodzenie pobierania z BGG bez konkretnego wyjątku.")

def parse_collection_data(root: ET.Element) -> Dict[str, ET.Element]:
    return {item.attrib['objectid']: item for item in root.findall("item")}

def extract_collection_basics(item: ET.Element) -> Dict[str, Any]:
    return {
        "title": item.findtext("name"),
        "year_published": int(item.findtext("yearpublished") or 0),
        "image": item.findtext("image"),
        "thumbnail": item.findtext("thumbnail"),
        "num_plays": int(item.findtext("numplays") or 0),
        "my_rating": (
            float(rating.attrib.get("value"))
            if (rating := item.find("stats/rating")) is not None and rating.attrib.get("value") not in [None, "N/A"]
            else None
        ),
        "average_rating": float(item.find("stats/rating/average").attrib.get("value", 0)) if item.find("stats/rating/average") is not None else None,
        "bgg_rank": int(item.find("stats/rating/ranks/rank").attrib.get("value")) if item.find("stats/rating/ranks/rank") is not None and item.find("stats/rating/ranks/rank").attrib.get("value").isdigit() else None,
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
    name = None
    for name_el in detail_item.findall("name"):
        if name_el.attrib.get("type") == "primary":
            name = name_el.attrib.get("value")
            break

    links = detail_item.findall("link")
    stats_el = detail_item.find("statistics/ratings")
    average_weight = None
    if stats_el is not None and stats_el.find("averageweight") is not None:
        try:
            average_weight = float(stats_el.find("averageweight").attrib.get("value"))
        except (ValueError, TypeError):
            average_weight = None

    return {
        "original_title": name,
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
    }

async def fetch_bgg_collection(username: str) -> None:
    log_info("📅 Rozpoczynam pobieranie kolekcji BGG")

    collection_url = f"{BGG_XML_BASE}/collection?username={username}&stats=1"
    thing_url_tmpl = f"{BGG_XML_BASE}/thing?id={{bgg_id}}&stats=1"

    async with _make_client() as client:
        collection_root = await fetch_xml(client, collection_url)
        collection_data = parse_collection_data(collection_root)

        log_info(f"🔍 Znaleziono {len(collection_data)} gier w kolekcji")

        for idx, (bgg_id, item) in enumerate(collection_data.items(), start=1):
            basic_data = extract_collection_basics(item)
            title = basic_data.get("title") or f"ID={bgg_id}"
            log_info(f"\n[{idx}/{len(collection_data)}] 🧩 Przetwarzam grę: {title} (ID={bgg_id})")

            detail_url = thing_url_tmpl.format(bgg_id=bgg_id)
            detail_root = await fetch_xml(client, detail_url)
            detail_item = detail_root.find("item")
            if not detail_item:
                log_info(f"⚠️ Pominięto grę {title} (ID={bgg_id}) - brak danych szczegółowych")
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
                    log_info(f"♻️ Zaktualizowano dane gry: {title}")
                else:
                    session.add(BGGGame(**full_data))
                    log_info(f"➕ Dodano nową grę: {title}")

                await session.commit()

            # krótka pauza „grzecznościowa” między /thing
            pause_time = 1.5
            log_info(f"⏳ Pauza {pause_time} s by uniknąć limitów BGG")
            await asyncio.sleep(pause_time)

    # Usuwanie gier, których już nie ma w kolekcji
    current_ids = {int(bgg_id) for bgg_id in collection_data.keys()}
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGGame.bgg_id))
        all_db_ids = set(result.scalars().all())

        to_delete = all_db_ids - current_ids
        if to_delete:
            await session.execute(BGGGame.__table__.delete().where(BGGGame.bgg_id.in_(to_delete)))
            await session.commit()
            log_info(f"🗑 Usunięto {len(to_delete)} gier spoza kolekcji")

    log_success("🎉 Zakończono przetwarzanie całej kolekcji BGG")
