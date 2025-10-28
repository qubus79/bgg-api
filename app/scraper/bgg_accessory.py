# app/scraper/bgg_accessory_scraper.py

import os
import httpx
import xml.etree.ElementTree as ET
from typing import Dict, Any
import asyncio
from app.database import AsyncSessionLocal
from app.models.bgg_accessory import BGGAccessory
from sqlalchemy import select
from app.utils.logging import log_info, log_success

# --- Konfiguracja HTTP / BGG ---
BGG_XML_BASE = "https://boardgamegeek.com/xmlapi2"
BGG_API_TOKEN = os.getenv("BGG_API_TOKEN")
USER_AGENT = "BoardGamesApp/1.0 (+contact: your-email@example.com)"

def _default_headers() -> Dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if BGG_API_TOKEN:
        headers["Authorization"] = f"Bearer {BGG_API_TOKEN}"
    return headers

def _make_client() -> httpx.AsyncClient:
    # Automatyczne HTTP/2 jeśli dostępny pakiet h2; inaczej spadek do HTTP/1.1
    want_http2 = os.getenv("HTTP2", "1") == "1"
    try:
        if want_http2:
            import h2  # noqa: F401
        http2_flag = want_http2
    except ImportError:
        http2_flag = False

    return httpx.AsyncClient(
        headers=_default_headers(),
        follow_redirects=True,
        http2=http2_flag,
        timeout=httpx.Timeout(30.0),
    )

# --- Helpers ---
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

# --- Pobieranie z retry/backoff ---
async def fetch_xml(client: httpx.AsyncClient, url: str) -> ET.Element:
    """
    Pobiera XML z obsługą:
    - 202 Accepted (kolejka) + Retry-After,
    - 429 Too Many Requests + Retry-After,
    - 5xx z backoffem,
    - 401/403 (błąd autoryzacji).
    """
    log_info(f"➡️ Fetching XML from: {url}")

    base_delay = 1.0
    max_attempts = 12
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = await client.get(url)

            if resp.status_code == 200:
                return ET.fromstring(resp.text)

            if resp.status_code == 202:
                delay_hdr = resp.headers.get("Retry-After")
                sleep_s = float(delay_hdr) if delay_hdr else base_delay * attempt
                log_info(f"⏳ 202 Accepted — czekam {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_s)
                continue

            if resp.status_code == 429:
                delay_hdr = resp.headers.get("Retry-After")
                sleep_s = float(delay_hdr) if delay_hdr else base_delay * attempt
                log_info(f"🚦 429 Too Many Requests — czekam {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_s)
                continue

            if resp.status_code in (500, 502, 503, 504):
                sleep_s = base_delay * attempt
                log_info(f"🛠 {resp.status_code} — retry za {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(sleep_s)
                continue

            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"BGG auth error {resp.status_code}. "
                    "Sprawdź BGG_API_TOKEN i czy aplikacja na BGG jest zatwierdzona."
                )

            resp.raise_for_status()

        except Exception as e:
            last_exc = e
            sleep_s = base_delay * attempt
            log_info(f"⚠️ Wyjątek {type(e).__name__}: {e} — retry za {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
            await asyncio.sleep(sleep_s)

    if last_exc:
        raise last_exc
    raise RuntimeError("Niepowodzenie pobierania z BGG bez konkretnego wyjątku.")

# --- Parsowanie ---
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

# --- Główna funkcja ---
async def fetch_bgg_accessories(username: str) -> None:
    log_info("📅 Rozpoczynam pobieranie akcesorii BGG")

    collection_url = f"{BGG_XML_BASE}/collection?username={username}&subtype=boardgameaccessory&stats=1"
    thing_url_tmpl = f"{BGG_XML_BASE}/thing?id={{bgg_id}}&stats=1"

    async with _make_client() as client:
        collection_root = await fetch_xml(client, collection_url)
        collection_data = parse_collection_data(collection_root)

        log_info(f"🔍 Znaleziono {len(collection_data)} akcesorii")

        for idx, (bgg_id, item) in enumerate(collection_data.items(), start=1):
            basic_data = extract_collection_basics(item)
            title = basic_data.get("name") or f"ID={bgg_id}"
            log_info(f"[{idx}/{len(collection_data)}] 🧰 Przetwarzam akcesorium: {title} (ID={bgg_id})")

            detail_url = thing_url_tmpl.format(bgg_id=bgg_id)
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

            # krótka pauza grzecznościowa między /thing
            pause_time = 1.5
            log_info(f"⏳ Pauza {pause_time} s by uniknąć limitów BGG")
            await asyncio.sleep(pause_time)

    # Usuwanie nieistniejących
    current_ids = {int(bgg_id) for bgg_id in collection_data.keys()}
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGAccessory.bgg_id))
        all_db_ids = set(result.scalars().all())
        to_delete = all_db_ids - current_ids
        if to_delete:
            await session.execute(BGGAccessory.__table__.delete().where(BGGAccessory.bgg_id.in_(to_delete)))
            await session.commit()
            log_info(f"🗑 Usunięto {len(to_delete)} nieistniejących akcesorii")

    log_success("🎉 Zakończono przetwarzanie kolekcji akcesorii BGG")
