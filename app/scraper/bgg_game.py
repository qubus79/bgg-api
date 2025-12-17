import os
import re
from datetime import datetime
import httpx
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional
import asyncio
from app.database import AsyncSessionLocal
from app.models.bgg_game import BGGGame
from sqlalchemy import select
from app.utils.logging import log_info, log_success

# New: BGG private collection data requires an authenticated session (cookies)
from app.services.bgg.auth_session import BGGAuthSessionManager

BGG_XML_BASE = "https://boardgamegeek.com/xmlapi2"
BGG_API_TOKEN = os.getenv("BGG_API_TOKEN")  # ustaw w .env / docker-compose
USER_AGENT = "BoardGamesApp/1.0 (+contact: your-email@example.com)"

# Private JSON collections endpoint (requires login cookies)
BGG_PRIVATE_BASE = "https://boardgamegeek.com"
BGG_PRIVATE_USER_ID = int(os.getenv("BGG_PRIVATE_USER_ID", "2382533"))

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

# -----------------------------
# Private purchase data helpers
# -----------------------------

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


# def _filter_to_model_fields(data: Dict[str, Any]) -> Dict[str, Any]:
#     """Keep only keys that exist as attributes on the SQLAlchemy model.

#     This makes scraper changes safe even before DB/model migrations land.
#     """
#     allowed = {}
#     for k, v in data.items():
#         if hasattr(BGGGame, k):
#             allowed[k] = v
#     return allowed

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

async def fetch_bgg_collection(username: str) -> None:
    log_info("📅 Rozpoczynam pobieranie kolekcji BGG")

    collection_url = f"{BGG_XML_BASE}/collection?username={username}&stats=1"
    thing_url_tmpl = f"{BGG_XML_BASE}/thing?id={{bgg_id}}&stats=1"

    async with _make_client() as client:
        auth = BGGAuthSessionManager()
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

            # Keep scraper safe even before DB/model migrations add the new fields
            # full_data_db = _filter_to_model_fields(full_data)
            # Scraper with working migration to DB of private data
            full_data_db = full_data

            async with AsyncSessionLocal() as session:
                result = await session.execute(select(BGGGame).where(BGGGame.bgg_id == int(bgg_id)))
                existing = result.scalars().first()

                if existing:
                    for field, value in full_data_db.items():
                        setattr(existing, field, value)
                    log_info(f"♻️ Zaktualizowano dane gry: {title}")
                else:
                    session.add(BGGGame(**full_data_db))
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
