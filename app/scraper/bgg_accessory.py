# app/scraper/bgg_accessory_scraper.py

import importlib.util
import os
import random
import httpx
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional, cast
import asyncio
from app.database import AsyncSessionLocal
from app.models.bgg_accessory import BGGAccessory
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.utils.convert import to_bool, to_float, to_int
from app.utils.logging import log_info, log_success
from app.utils.model_helpers import apply_model_fields


# =============================================================================
# CONFIGURATION
# =============================================================================

BGG_XML_BASE = "https://boardgamegeek.com/xmlapi2"
BGG_API_TOKEN = os.getenv("BGG_API_TOKEN")
USER_AGENT = "BoardGamesApp/1.0 (+contact: your-email@example.com)"
THING_URL_TMPL = f"{BGG_XML_BASE}/thing?id={{bgg_id}}&stats=1"
ACCESSORY_DETAIL_CONCURRENCY = int(os.getenv("BGG_ACCESSORY_DETAIL_CONCURRENCY", "2"))
ACCESSORY_THING_PAUSE_SECONDS = float(os.getenv("BGG_ACCESSORY_THING_PAUSE_SECONDS", "1.5"))
BGG_REQUEST_PAUSE_SECONDS = float(os.getenv("BGG_REQUEST_PAUSE_SECONDS", "0.3"))
BGG_REQUEST_JITTER_SECONDS = float(os.getenv("BGG_REQUEST_JITTER_SECONDS", "0.2"))


# =============================================================================
# HTTP HELPERS
# =============================================================================

def _default_headers() -> Dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if BGG_API_TOKEN:
        headers["Authorization"] = f"Bearer {BGG_API_TOKEN}"
    return headers


def _make_client() -> httpx.AsyncClient:
    want_http2 = os.getenv("HTTP2", "1") == "1"
    http2_flag = want_http2 and importlib.util.find_spec("h2") is not None

    return httpx.AsyncClient(
        headers=_default_headers(),
        follow_redirects=True,
        http2=http2_flag,
        timeout=httpx.Timeout(30.0),
    )


# =============================================================================
# RETRY / BACKOFF HANDLING
# =============================================================================

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
                root = ET.fromstring(resp.text)
                await asyncio.sleep(BGG_REQUEST_PAUSE_SECONDS + random.uniform(0, BGG_REQUEST_JITTER_SECONDS))
                return root

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


# =============================================================================
# PARSING HELPERS
# =============================================================================

def parse_collection_data(root: ET.Element) -> Dict[str, ET.Element]:
    return {item.attrib['objectid']: item for item in root.findall("item")}


def _element_value(element: Optional[ET.Element], attr: str = "value") -> Optional[str]:
    if element is None:
        return None
    return element.attrib.get(attr)


def extract_collection_basics(item: ET.Element) -> Dict[str, Any]:
    status = item.find("status")
    rating_el = item.find("stats/rating")
    average_rating_el = item.find("stats/rating/average")
    rank_el = item.find("stats/rating/ranks/rank")
    return {
        "name": item.findtext("name"),
        "year_published": to_int(item.findtext("yearpublished")),
        "image": item.findtext("image"),
        "num_plays": to_int(item.findtext("numplays")),
        "my_rating": to_float(_element_value(rating_el)),
        "average_rating": to_float(_element_value(average_rating_el)),
        "bgg_rank": to_int(_element_value(rank_el)),
        "owned": bool(to_bool(_element_value(status, "own"))),
        "preordered": bool(to_bool(_element_value(status, "preordered"))),
        "wishlist": bool(to_bool(_element_value(status, "wishlist"))),
        "want_to_buy": bool(to_bool(_element_value(status, "wanttobuy"))),
        "want_to_play": bool(to_bool(_element_value(status, "wanttoplay"))),
        "want": bool(to_bool(_element_value(status, "want"))),
        "for_trade": bool(to_bool(_element_value(status, "fortrade"))),
        "previously_owned": bool(to_bool(_element_value(status, "prevowned"))),
        "last_modified": _element_value(status, "lastmodified"),
    }


def extract_details(detail_item: ET.Element) -> Dict[str, Any]:
    publisher_links = [
        value
        for value in (
            l.attrib.get("value")
            for l in detail_item.findall("link")
            if l.attrib.get("type") == "boardgamepublisher"
        )
        if value
    ]
    publisher_str = ", ".join(publisher_links)
    return {
        "description": detail_item.findtext("description"),
        "publisher": publisher_str,
    }


# =============================================================================
# PAYLOAD BUILDERS
# =============================================================================

async def _build_accessory_payload(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    idx: int,
    total: int,
    bgg_id: str,
    basic_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    title = basic_data.get("name") or f"ID={bgg_id}"
    detail_url = THING_URL_TMPL.format(bgg_id=bgg_id)

    async with sem:
        log_info(f"[{idx}/{total}] 🧰 Przetwarzam akcesorium: {title} (ID={bgg_id})")
        detail_root = await fetch_xml(client, detail_url)
        detail_item = detail_root.find("item")
        if not detail_item:
            log_info(f"⚠️ Pominięto {title} (ID={bgg_id}) - brak danych szczegółowych")
            return None

        detailed_data = extract_details(detail_item)
        full_data = {
            "bgg_id": int(bgg_id),
            **basic_data,
            **detailed_data,
        }

    await asyncio.sleep(ACCESSORY_THING_PAUSE_SECONDS)
    return full_data


# =============================================================================
# DATA PERSISTENCE
# =============================================================================

async def _persist_accessories(
    accessories_data: List[Dict[str, Any]],
    collection_ids: set[int],
) -> tuple[int, int, int]:

    inserted = 0
    updated = 0
    deleted = 0

    session = AsyncSessionLocal()
    session = cast(AsyncSession, session)
    try:
        new_ids = {item["bgg_id"] for item in accessories_data}
        existing = {}
        if new_ids:
            result = await session.execute(select(BGGAccessory).where(BGGAccessory.bgg_id.in_(new_ids)))
            existing = {item.bgg_id: item for item in result.scalars().all()}

        for data in accessories_data:
            bgg_id = data["bgg_id"]
            title = data.get("name") or f"ID={bgg_id}"
            model = existing.get(bgg_id)
            if model:
                apply_model_fields(model, data)
                log_info(f"♻️ Zaktualizowano dane akcesorium: {title}")
                updated += 1
            else:
                session.add(BGGAccessory(**data))
                log_info(f"➕ Dodano nowe akcesorium: {title}")
                inserted += 1

        result = await session.execute(select(BGGAccessory.bgg_id))
        all_db_ids = set(result.scalars().all())
        to_delete = all_db_ids - collection_ids
        if to_delete:
            await session.execute(delete(BGGAccessory).where(BGGAccessory.bgg_id.in_(to_delete)))
            deleted = len(to_delete)

        await session.commit()
    finally:
        await session.close()

    return inserted, updated, deleted


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

async def fetch_bgg_accessories(username: str) -> None:
    log_info("📅 Rozpoczynam pobieranie akcesorii BGG")

    collection_url = f"{BGG_XML_BASE}/collection?username={username}&subtype=boardgameaccessory&stats=1"

    async with _make_client() as client:
        collection_root = await fetch_xml(client, collection_url)
        collection_data = parse_collection_data(collection_root)

        log_info(f"🔍 Znaleziono {len(collection_data)} akcesorii")

        collection_items = list(collection_data.items())
        collection_ids = {int(bgg_id) for bgg_id in collection_data.keys() if bgg_id is not None}
        sem = asyncio.Semaphore(ACCESSORY_DETAIL_CONCURRENCY)
        tasks = []

        for idx, (bgg_id, item) in enumerate(collection_items, start=1):
            basic_data = extract_collection_basics(item)
            tasks.append(
                _build_accessory_payload(client, sem, idx, len(collection_items), bgg_id, basic_data)
            )

        results = await asyncio.gather(*tasks)
        accessories_data = [result for result in results if result is not None]
        inserted, updated, deleted = await _persist_accessories(accessories_data, collection_ids)

    log_success(
        f"🎉 Akcesoria BGG zostały zsynchronizowane z bazą danych (inserted={inserted}, updated={updated}, removed={deleted})"
    )
