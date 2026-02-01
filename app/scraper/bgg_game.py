import os
import random
import re
from datetime import datetime
import httpx
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List, cast
import asyncio
from app.database import AsyncSessionLocal
from app.models.bgg_game import BGGGame
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.utils.convert import to_bool, to_float, to_int
from app.utils.logging import log_info, log_success
from app.utils.model_helpers import apply_model_fields

# New: BGG private collection data requires an authenticated session (cookies)
from app.services.bgg.auth_session import BGGAuthSessionManager


# =============================================================================
# CONFIGURATION
# =============================================================================

BGG_XML_BASE = "https://boardgamegeek.com/xmlapi2"
BGG_API_TOKEN = os.getenv("BGG_API_TOKEN")  # ustaw w .env / docker-compose
USER_AGENT = "BoardGamesApp/1.0 (+contact: your-email@example.com)"

BGG_PRIVATE_BASE = "https://boardgamegeek.com"
BGG_PRIVATE_USER_ID = int(os.getenv("BGG_PRIVATE_USER_ID", "2382533"))
DETAIL_CONCURRENCY = int(os.getenv("BGG_DETAIL_CONCURRENCY", "1"))
THING_REQUEST_PAUSE_SECONDS = float(os.getenv("BGG_THING_PAUSE_SECONDS", "1.5"))
THING_URL_TMPL = f"{BGG_XML_BASE}/thing?id={{bgg_id}}&stats=1"
BGG_REQUEST_PAUSE_SECONDS = float(os.getenv("BGG_REQUEST_PAUSE_SECONDS", "0.8"))
BGG_REQUEST_JITTER_SECONDS = float(os.getenv("BGG_REQUEST_JITTER_SECONDS", "0.4"))
BGG_REQUEST_BACKOFF_FACTOR = float(os.getenv("BGG_REQUEST_BACKOFF_FACTOR", "2"))


# =============================================================================
# HTTP HELPERS
# =============================================================================

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


# =============================================================================
# RETRY / BACKOFF HANDLING
# =============================================================================

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


# =============================================================================
# COLLECTION PARSING HELPERS
# =============================================================================

def parse_collection_data(root: ET.Element) -> Dict[str, ET.Element]:
    return {item.attrib['objectid']: item for item in root.findall("item")}


def _element_value(element: Optional[ET.Element], attr: str = "value") -> Optional[str]:
    if element is None:
        return None
    return element.attrib.get(attr)


def _rating_value(element: Optional[ET.Element]) -> Optional[str]:
    if element is None:
        return None
    value = element.attrib.get("value")
    if value in (None, "N/A"):
        return None
    return value


def extract_collection_basics(item: ET.Element) -> Dict[str, Any]:
    status_el = item.find("status")
    rating_el = item.find("stats/rating")
    average_rating_el = item.find("stats/rating/average")
    rank_el = item.find("stats/rating/ranks/rank")

    return {
        "title": item.findtext("name"),
        "year_published": to_int(item.findtext("yearpublished")),
        "image": item.findtext("image"),
        "thumbnail": item.findtext("thumbnail"),
        "num_plays": to_int(item.findtext("numplays")),
        "my_rating": to_float(_rating_value(rating_el)),
        "average_rating": to_float(_element_value(average_rating_el)),
        "bgg_rank": to_int(_element_value(rank_el)),
        "status_owned": bool(to_bool(_element_value(status_el, "own"))),
        "status_preordered": bool(to_bool(_element_value(status_el, "preordered"))),
        "status_wishlist": bool(to_bool(_element_value(status_el, "wishlist"))),
        "status_fortrade": bool(to_bool(_element_value(status_el, "fortrade"))),
        "status_prevowned": bool(to_bool(_element_value(status_el, "prevowned"))),
        "status_wanttoplay": bool(to_bool(_element_value(status_el, "wanttoplay"))),
        "status_wanttobuy": bool(to_bool(_element_value(status_el, "wanttobuy"))),
        "status_wishlist_priority": to_int(_element_value(status_el, "wishlistpriority")),
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
            average_weight = to_float(_element_value(stats_el.find("averageweight")))
        except (ValueError, TypeError):
            average_weight = None

    return {
        "original_title": name,
        "description": detail_item.findtext("description"),
        "mechanics": [value for value in (l.attrib.get("value") for l in links if l.attrib.get("type") == "boardgamemechanic") if value],
        "designers": [value for value in (l.attrib.get("value") for l in links if l.attrib.get("type") == "boardgamedesigner") if value],
        "artists": [value for value in (l.attrib.get("value") for l in links if l.attrib.get("type") == "boardgameartist") if value],
        "min_players": to_int(_element_value(detail_item.find("minplayers"))),
        "max_players": to_int(_element_value(detail_item.find("maxplayers"))),
        "min_playtime": to_int(_element_value(detail_item.find("minplaytime"))),
        "max_playtime": to_int(_element_value(detail_item.find("maxplaytime"))),
        "play_time": to_int(_element_value(detail_item.find("playingtime"))),
        "min_age": to_int(_element_value(detail_item.find("minage"))),
        "type": detail_item.attrib.get("type", None),
        "weight": average_weight,
    }


# =============================================================================
# PRIVATE PURCHASE DATA HELPERS
# =============================================================================

_CURRENCY_HINT_RE = re.compile(r"Currency:\s*([A-Z]{3})")


def normalize_purchase_currency(pp_currency: Optional[str], private_comment: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return (currency_code, source) according to the rules:

    - pp_currency in {USD, CAD, AUD, YEN, GPB/GBP, EUR} maps to currency codes (YEN->JPY, GPB->GBP).
    - If price is present but pp_currency is null, default PLN unless privatecomment contains 'Currency: XXX'.

    NOTE: This function returns only currency and source. The caller decides whether a price is present.
    """

    if pp_currency:
        v = pp_currency.strip().upper()
        if v == "YEN":
            return "JPY", "pp_currency"
        if v in {"USD", "CAD", "AUD", "EUR", "GBP"}:
            return v, "pp_currency"
        # Unknown/other: keep as-is but mark source
        return v, "pp_currency"

    # No pp_currency — allow override from private comment
    if private_comment:
        m = _CURRENCY_HINT_RE.search(private_comment)
        if m:
            return m.group(1).upper(), "privatecomment"

    # Caller may only want PLN default when pricepaid exists; still return PLN + source for convenience.
    return "PLN", "default_pln"


async def fetch_private_collection_item(
    client: httpx.AsyncClient,
    auth: BGGAuthSessionManager,
    bgg_id: int,
) -> Optional[Dict[str, Any]]:
    """Fetch private collection JSON for a single game.

    Requires a valid logged-in BGG session (cookies). Uses a single automatic re-login on 401/403.
    """

    url = f"{BGG_PRIVATE_BASE}/api/collections?objectid={bgg_id}&objecttype=thing&userid={BGG_PRIVATE_USER_ID}"

    # Ensure cookies are present on this client
    await auth.ensure_session(client)

    resp = await client.get(url)

    # If auth expired, retry once after re-login
    if resp.status_code in (401, 403):
        log_info(f"🔐 Private collections returned {resp.status_code} for {bgg_id} — re-login and retry once")
        await auth.invalidate()
        await auth.ensure_session(client)
        resp = await client.get(url)

    if resp.status_code != 200:
        log_info(f"⚠️ Private collections HTTP {resp.status_code} for {bgg_id} — skipping private fields")
        return None

    try:
        payload = resp.json()
    except Exception as e:
        log_info(f"⚠️ Private collections JSON parse error for {bgg_id}: {e}")
        return None

    items = payload.get("items") if isinstance(payload, dict) else None
    if not items or not isinstance(items, list):
        return None

    item = items[0] if items else None
    if not isinstance(item, dict):
        return None

    pp_currency = item.get("pp_currency")
    pricepaid = item.get("pricepaid")
    quantity = item.get("quantity")
    # BGG usually returns acquisitiondate as YYYY-MM-DD
    raw_acquisitiondate = item.get("acquisitiondate")
    acquisitiondate = None
    if raw_acquisitiondate:
        try:
            acquisitiondate = datetime.strptime(str(raw_acquisitiondate), "%Y-%m-%d")
        except Exception:
            # If format is unexpected, keep it null (do not break the sync)
            acquisitiondate = None

    acquiredfrom = item.get("acquiredfrom")
    privatecomment = item.get("privatecomment")

    # Currency normalization rules
    purchase_currency = None
    purchase_currency_source = None

    if pricepaid is not None:
        purchase_currency, purchase_currency_source = normalize_purchase_currency(pp_currency, privatecomment)
    else:
        # Keep currency null if there is no price (unless BGG provides explicit pp_currency)
        if pp_currency:
            purchase_currency, purchase_currency_source = normalize_purchase_currency(pp_currency, privatecomment)

    return {
        "purchase_currency": purchase_currency,
        "purchase_currency_source": purchase_currency_source,
        "purchase_price_paid": pricepaid,
        "purchase_quantity": quantity,
        "purchase_acquisition_date": acquisitiondate,
        "purchase_acquired_from": acquiredfrom,
        "purchase_private_comment": privatecomment,
    }


# =============================================================================
# PAYLOAD BUILDERS
# =============================================================================

async def _build_game_payload(
    client: httpx.AsyncClient,
    auth: BGGAuthSessionManager,
    sem: asyncio.Semaphore,
    idx: int,
    total: int,
    bgg_id: str,
    basic_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    title = basic_data.get("title") or f"ID={bgg_id}"
    detail_url = THING_URL_TMPL.format(bgg_id=bgg_id)

    async with sem:
        log_info(f"\n[{idx}/{total}] 🧩 Przetwarzam grę: {title} (ID={bgg_id})")
        detail_root = await fetch_xml(client, detail_url)
        detail_item = detail_root.find("item")
        if not detail_item:
            log_info(f"⚠️ Pominięto grę {title} (ID={bgg_id}) - brak danych szczegółowych")
            return None

        detailed_data = extract_details(detail_item)

        private_data = await fetch_private_collection_item(client, auth, int(bgg_id))
        if private_data:
            log_info("🔒 Private purchase fields: available")
        else:
            log_info("🔒 Private purchase fields: not available")

        full_data = {
            "bgg_id": int(bgg_id),
            **basic_data,
            **detailed_data,
            **(private_data or {}),
        }

    await asyncio.sleep(THING_REQUEST_PAUSE_SECONDS)
    return full_data


# =============================================================================
# DATA PERSISTENCE
# =============================================================================

async def _persist_games(
    games_data: List[Dict[str, Any]],
    collection_ids: set[int],
) -> tuple[int, int, int]:

    inserted = 0
    updated = 0
    deleted = 0

    session = AsyncSessionLocal()
    session = cast(AsyncSession, session)
    try:
        new_ids = {game["bgg_id"] for game in games_data}
        existing = {}
        if new_ids:
            result = await session.execute(select(BGGGame).where(BGGGame.bgg_id.in_(new_ids)))
            existing = {game.bgg_id: game for game in result.scalars().all()}

        for data in games_data:
            bgg_id = data["bgg_id"]
            title = data.get("title") or data.get("name") or f"BGG ID {bgg_id}"
            model = existing.get(bgg_id)
            if model:
                apply_model_fields(model, data)
                log_info(f"♻️ Zaktualizowano dane gry: {title}")
                updated += 1
            else:
                session.add(BGGGame(**data))
                log_info(f"➕ Dodano nową grę: {title}")
                inserted += 1

        result = await session.execute(select(BGGGame.bgg_id))
        all_db_ids = set(result.scalars().all())
        to_delete = all_db_ids - collection_ids
        if to_delete:
            await session.execute(delete(BGGGame).where(BGGGame.bgg_id.in_(to_delete)))
            deleted = len(to_delete)

        await session.commit()
    finally:
        await session.close()

    return inserted, updated, deleted


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

async def fetch_bgg_collection(username: str) -> None:
    log_info("📅 Rozpoczynam pobieranie kolekcji BGG")

    collection_url = f"{BGG_XML_BASE}/collection?username={username}&stats=1"

    async with _make_client() as client:
        auth = BGGAuthSessionManager()
        collection_root = await fetch_xml(client, collection_url)
        collection_data = parse_collection_data(collection_root)

        log_info(f"🔍 Znaleziono {len(collection_data)} gier w kolekcji")

        collection_items = list(collection_data.items())
        collection_ids = {int(bgg_id) for bgg_id in collection_data.keys() if bgg_id is not None}
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
        tasks = []

        for idx, (bgg_id, item) in enumerate(collection_items, start=1):
            basic_data = extract_collection_basics(item)
            tasks.append(_build_game_payload(client, auth, sem, idx, len(collection_items), bgg_id, basic_data))

        results = await asyncio.gather(*tasks)
        games_data = [result for result in results if result is not None]
        inserted, updated, deleted = await _persist_games(games_data, collection_ids)

    log_success(
        f"🎉 Kolekcja BGG została zsynchronizowana z bazą danych (inserted={inserted}, updated={updated}, removed={deleted})"
    )
