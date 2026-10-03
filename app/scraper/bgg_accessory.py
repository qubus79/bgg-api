# app/scraper/bgg_accessory_scraper.py

import importlib.util
import os
import random
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, Any, List, Optional, cast
import asyncio
from app.database import AsyncSessionLocal
from app.models.bgg_accessory import BGGAccessory
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.utils.bgg_hash_cache import BGGHashCache, build_hash_cache, compute_payload_hash
from app.utils.convert import to_bool, to_float, to_int
from app.utils.logging import log_info, log_success
from app.utils.telegram_notify import send_scrape_message
from app.utils.model_helpers import apply_model_fields


# =============================================================================
# CONFIGURATION
# =============================================================================

BGG_XML_BASE = "https://boardgamegeek.com/xmlapi2"
BGG_API_TOKEN = os.getenv("BGG_API_TOKEN")
USER_AGENT = "BoardGamesApp/1.0 (+contact: your-email@example.com)"
THING_URL_TMPL = f"{BGG_XML_BASE}/thing?id={{ids}}&stats=1"
# BGG `thing` przyjmuje do 20 identyfikatorów naraz — jedno zapytanie na
# paczkę zamiast jednego na akcesorium.
THING_BATCH_SIZE = 20
ACCESSORY_THING_PAUSE_SECONDS = float(os.getenv("BGG_ACCESSORY_THING_PAUSE_SECONDS", "1.5"))
FETCH_MAX_ATTEMPTS = int(os.getenv("BGG_FETCH_MAX_ATTEMPTS", "6"))
BGG_REQUEST_PAUSE_SECONDS = float(os.getenv("BGG_REQUEST_PAUSE_SECONDS", "0.3"))
BGG_REQUEST_JITTER_SECONDS = float(os.getenv("BGG_REQUEST_JITTER_SECONDS", "0.2"))
BGG_REQUEST_BACKOFF_FACTOR = float(os.getenv("BGG_REQUEST_BACKOFF_FACTOR", "1.5"))


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

class BGGAuthError(RuntimeError):
    """401/403 — ponawianie nic nie da, trzeba poprawić token."""


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
    max_attempts = FETCH_MAX_ATTEMPTS
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = await client.get(url)

            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                await asyncio.sleep(BGG_REQUEST_PAUSE_SECONDS + random.uniform(0, BGG_REQUEST_JITTER_SECONDS))
                return root

            if resp.status_code == 202:
                delay = float(resp.headers.get("Retry-After", base_delay * (BGG_REQUEST_BACKOFF_FACTOR ** (attempt - 1))))
                log_info(f"⏳ 202 Accepted — czekam {delay:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(delay)
                continue

            if resp.status_code == 429:
                delay = base_delay * (BGG_REQUEST_BACKOFF_FACTOR ** (attempt - 1))
                jitter = random.uniform(0, BGG_REQUEST_JITTER_SECONDS)
                log_info(f"🚦 429 Too Many Requests — czekam {delay + jitter:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(delay + jitter)
                continue

            if resp.status_code in (500, 502, 503, 504):
                delay = base_delay * (BGG_REQUEST_BACKOFF_FACTOR ** (attempt - 1))
                log_info(f"🛠 {resp.status_code} — retry za {delay:.1f}s (attempt {attempt}/{max_attempts})")
                await asyncio.sleep(delay)
                continue

            if resp.status_code in (401, 403):
                raise BGGAuthError(
                    f"BGG auth error {resp.status_code}. "
                    "Sprawdź BGG_API_TOKEN i czy aplikacja na BGG jest zatwierdzona."
                )

            resp.raise_for_status()

        except (BGGAuthError, httpx.HTTPStatusError):
            # Błąd autoryzacji i pozostałe 4xx nie mijają same — bez ponawiania.
            raise
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
# SZCZEGÓŁY: TYLKO NOWE I ZMIENIONE, PACZKAMI
# =============================================================================

def ids_needing_details(
    basics: Dict[str, Dict[str, Any]],
    known: Dict[int, Dict[str, Any]],
) -> List[str]:
    """Akcesoria, dla których trzeba pobrać `thing` (opis, wydawca).

    Kolekcja podaje `lastmodified` każdej pozycji. Gdy jest taki sam jak
    w bazie, opis i wydawca też się nie zmieniły — bierzemy je z bazy zamiast
    pytać BGG. Nowe pozycje i pozycje bez daty zawsze idą do pobrania.
    """
    needed: List[str] = []
    for bgg_id, basic in basics.items():
        row = known.get(int(bgg_id))
        if row is None:
            needed.append(bgg_id)
            continue
        modified = basic.get("last_modified")
        if not modified or modified != row.get("last_modified"):
            needed.append(bgg_id)
    return needed


def chunks(ids: List[str], size: int = THING_BATCH_SIZE) -> List[List[str]]:
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def parse_thing_batch(root: ET.Element) -> Dict[str, Dict[str, Any]]:
    """Odpowiedź `thing` dla wielu id → szczegóły po id."""
    return {
        item.attrib["id"]: extract_details(item)
        for item in root.findall("item")
        if item.attrib.get("id")
    }


async def _load_known_details() -> Dict[int, Dict[str, Any]]:
    """Akcesoria z bazy: data zmiany i szczegóły pobrane wcześniej."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(
                BGGAccessory.bgg_id,
                BGGAccessory.last_modified,
                BGGAccessory.description,
                BGGAccessory.publisher,
            )
        )
        return {
            row.bgg_id: {
                "last_modified": row.last_modified,
                "description": row.description,
                "publisher": row.publisher,
            }
            for row in result.all()
        }


# =============================================================================
# DATA PERSISTENCE
# =============================================================================

async def _persist_accessories(
    accessories_data: List[Dict[str, Any]],
    collection_ids: set[int],
    hash_cache: BGGHashCache | None,
) -> tuple[int, int, int, int, List[str], List[str], List[str], List[str]]:

    inserted = 0
    updated = 0
    deleted = 0
    inserted_titles: List[str] = []
    updated_titles: List[str] = []
    deleted_titles: List[str] = []
    skipped = 0
    skipped_titles: List[str] = []

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
            payload_hash: str | None = None
            if hash_cache:
                payload_hash = compute_payload_hash(data)
                cached_hash = await hash_cache.get_hash("accessory", bgg_id)
                if cached_hash == payload_hash and model:
                    skipped += 1
                    skipped_titles.append(title)
                    log_info(f"🛡️ {title} (ID={bgg_id}) — hash niezmieniony, pomijam zapis")
                    continue

            if model:
                apply_model_fields(model, data)
                log_info(f"♻️ Zaktualizowano dane akcesorium: {title}")
                updated += 1
                updated_titles.append(title)
                if hash_cache and payload_hash:
                    await hash_cache.set_hash("accessory", bgg_id, payload_hash)
            else:
                session.add(BGGAccessory(**data))
                log_info(f"➕ Dodano nowe akcesorium: {title}")
                inserted += 1
                inserted_titles.append(title)
                if hash_cache and payload_hash:
                    await hash_cache.set_hash("accessory", bgg_id, payload_hash)

        result = await session.execute(select(BGGAccessory.bgg_id))
        all_db_ids = set(result.scalars().all())
        to_delete = all_db_ids - collection_ids
        if to_delete:
            result = await session.execute(select(BGGAccessory.bgg_id, BGGAccessory.name).where(BGGAccessory.bgg_id.in_(to_delete)))
            deleted_titles.extend([row[1] or f"BGG ID {row[0]}" for row in result.all()])
            await session.execute(delete(BGGAccessory).where(BGGAccessory.bgg_id.in_(to_delete)))
            deleted = len(to_delete)
            if hash_cache:
                for removed_id in to_delete:
                    await hash_cache.delete_hash("accessory", removed_id)

        await session.commit()
    finally:
        await session.close()

    return inserted, updated, deleted, skipped, inserted_titles, updated_titles, deleted_titles, skipped_titles


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

async def fetch_bgg_accessories(username: str, ctx=None) -> None:
    if ctx is None:
        from app import jobs
        ctx = jobs.NULL_CTX

    log_info("📅 Rozpoczynam pobieranie akcesorii BGG")
    start_time = datetime.utcnow()

    collection_url = f"{BGG_XML_BASE}/collection?username={username}&subtype=boardgameaccessory&stats=1"

    async with _make_client() as client:
        ctx.set_stage("fetch_remote", detail=f"kolekcja {username}", index=1, count=3)
        collection_root = await fetch_xml(client, collection_url)
        collection_data = parse_collection_data(collection_root)

        log_info(f"🔍 Znaleziono {len(collection_data)} akcesorii")

        collection_ids = {int(bgg_id) for bgg_id in collection_data.keys() if bgg_id is not None}
        basics = {bgg_id: extract_collection_basics(item) for bgg_id, item in collection_data.items()}
        known = await _load_known_details()
        needed = ids_needing_details(basics, known)
        log_info(f"🧰 Szczegóły do pobrania: {len(needed)} z {len(basics)} (reszta bez zmian od ostatniego razu)")

        # Paczki po 20 — tu mija większość czasu, więc raportujemy postęp
        # po każdej paczce.
        ctx.set_stage("fetch_details", total=len(needed), unit="accessories", index=2, count=3)
        details: Dict[str, Dict[str, Any]] = {}
        batches = chunks(needed)
        for number, batch in enumerate(batches, start=1):
            root = await fetch_xml(client, THING_URL_TMPL.format(ids=",".join(batch)))
            details.update(parse_thing_batch(root))
            ctx.set_progress(min(number * THING_BATCH_SIZE, len(needed)))
            if number < len(batches):
                await asyncio.sleep(ACCESSORY_THING_PAUSE_SECONDS)

        accessories_data: List[Dict[str, Any]] = []
        for bgg_id, basic in basics.items():
            if bgg_id in details:
                extra = details[bgg_id]
            elif int(bgg_id) in known and bgg_id not in needed:
                row = known[int(bgg_id)]
                extra = {"description": row["description"], "publisher": row["publisher"]}
            else:
                log_info(f"⚠️ Pominięto {basic.get('name') or bgg_id} (ID={bgg_id}) - brak danych szczegółowych")
                continue
            accessories_data.append({"bgg_id": int(bgg_id), **basic, **extra})

        hash_cache = await build_hash_cache()
        if hash_cache is None:
            log_info("🗂️ Hash cache Redis nie został skonfigurowany; każdy rekord będzie zapisywany.")
        ctx.set_stage("db_sync", total=len(accessories_data), unit="accessories", index=3, count=3)
        inserted, updated, deleted, skipped, inserted_titles, updated_titles, deleted_titles, skipped_titles = await _persist_accessories(
            accessories_data, collection_ids, hash_cache
        )
        ctx.set_progress(len(accessories_data))
        ctx.set_counters(
            total=len(accessories_data),
            inserted=inserted,
            updated=updated,
            removed=deleted,
            skipped=skipped,
        )

    log_success(
        f"🎉 Akcesoria BGG zostały zsynchronizowane z bazą danych (inserted={inserted}, updated={updated}, removed={deleted})"
    )
    end_time = datetime.utcnow()
    stats = {
        "Total accessories": len(accessories_data),
        "Added": inserted,
        "Updated": updated,
        "Removed": deleted,
        "Unchanged accessories": skipped,
    }
    details = {
        "Added accessories": inserted_titles,
        "Updated accessories": updated_titles,
        "Removed accessories": deleted_titles,
    }
    await send_scrape_message("BGG accessories sync", "✅ SUCCESS", start_time, end_time, stats, details)
