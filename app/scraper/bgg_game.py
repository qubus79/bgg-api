import os
import random
import re
from datetime import datetime
import httpx
import xml.etree.ElementTree as ET
from typing import Callable, Dict, Any, Optional, List, Tuple, cast
import asyncio
from app.database import AsyncSessionLocal
from app.models.bgg_game import BGGGame
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.utils.convert import to_bool, to_float, to_int
from app.utils.logging import log_info, log_success
from app.utils.model_helpers import apply_model_fields
from app.utils.telegram_notify import send_scrape_message
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RESET = "\033[0m"

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
# Szczegóły gier paczkami — jedno zapytanie `thing` na 20 gier zamiast na grę.
THING_BATCH_SIZE = int(os.getenv("BGG_THING_BATCH_SIZE", "20"))
THING_BATCH_URL_TMPL = f"{BGG_XML_BASE}/thing?id={{ids}}&stats=1"
# Szczegóły (opis, mechaniki, wydawcy, waga…) zmieniają się rzadko — odświeżamy
# je, gdy są starsze niż tyle dni. Ocena, ranking i partie idą z kolekcji
# przy każdym syncu.
DETAILS_MAX_AGE_DAYS = int(os.getenv("BGG_DETAILS_MAX_AGE_DAYS", "30"))
PRIVATE_PAUSE_SECONDS = float(os.getenv("BGG_PRIVATE_PAUSE_SECONDS", "0.5"))
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

class BGGAuthError(RuntimeError):
    """401/403 — ponawianie nic nie da, przerywamy od razu."""


# BGG odpowiada 202, gdy dopiero przygotowuje dane (zwykle kolekcję). Pytamy
# wtedy co kilka sekund — wcześniej odstęp rósł wykładniczo (1, 2, 4 … 64 s)
# i sam pierwszy krok potrafił trwać kilka minut.
QUEUED_POLL_SECONDS = float(os.getenv("BGG_QUEUED_POLL_SECONDS", "4"))
QUEUED_MAX_ATTEMPTS = int(os.getenv("BGG_QUEUED_MAX_ATTEMPTS", "45"))
RETRY_MAX_ATTEMPTS = int(os.getenv("BGG_FETCH_MAX_ATTEMPTS", "6"))
RETRY_MAX_DELAY_SECONDS = float(os.getenv("BGG_RETRY_MAX_DELAY_SECONDS", "60"))


async def fetch_xml(
    client: httpx.AsyncClient,
    url: str,
    on_wait: Optional[Callable[[str], None]] = None,
) -> ET.Element:
    """
    Pobierz XML z obsługą:
    - 202 Accepted (BGG przygotowuje dane) — pytamy co `QUEUED_POLL_SECONDS`,
    - 429 Too Many Requests i 5xx — backoff z górnym limitem,
    - 401/403 — bez ponawiania.
    `on_wait` dostaje opis oczekiwania (do postępu zadania).
    """
    log_info(f"➡️ Fetching XML from: {url}")

    base_delay = 1.0
    queued = 0
    failures = 0
    last_exc: Exception | None = None

    while True:
        try:
            resp = await client.get(url)

            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                await asyncio.sleep(BGG_REQUEST_PAUSE_SECONDS + random.uniform(0, BGG_REQUEST_JITTER_SECONDS))
                return root

            if resp.status_code == 202:
                queued += 1
                if queued > QUEUED_MAX_ATTEMPTS:
                    raise RuntimeError(f"BGG nadal przygotowuje dane po {queued - 1} próbach — spróbuj później.")
                delay = float(resp.headers.get("Retry-After", QUEUED_POLL_SECONDS))
                message = f"BGG is preparing the data, waiting ({queued})"
                log_info(f"⏳ 202 Accepted — BGG przygotowuje dane, czekam {delay:.0f}s (próba {queued}/{QUEUED_MAX_ATTEMPTS})")
                if on_wait:
                    on_wait(message)
                await asyncio.sleep(delay)
                continue

            if resp.status_code in (401, 403):
                raise BGGAuthError(
                    f"BGG auth error {resp.status_code}. "
                    "Sprawdź BGG_API_TOKEN i czy aplikacja na BGG jest zatwierdzona."
                )

            if resp.status_code == 429 or resp.status_code in (500, 502, 503, 504):
                failures += 1
                if failures >= RETRY_MAX_ATTEMPTS:
                    resp.raise_for_status()
                delay = min(base_delay * (BGG_REQUEST_BACKOFF_FACTOR ** (failures - 1)), RETRY_MAX_DELAY_SECONDS)
                delay += random.uniform(0, BGG_REQUEST_JITTER_SECONDS)
                log_info(f"🚦 HTTP {resp.status_code} — retry za {delay:.1f}s (próba {failures}/{RETRY_MAX_ATTEMPTS})")
                if on_wait:
                    on_wait(f"BGG answered {resp.status_code}, retrying ({failures})")
                await asyncio.sleep(delay)
                continue

            # Inne kody — przerwij standardowym wyjątkiem
            resp.raise_for_status()
            raise RuntimeError(f"Nieoczekiwana odpowiedź BGG: HTTP {resp.status_code}")

        except (BGGAuthError, httpx.HTTPStatusError):
            raise
        except RuntimeError:
            raise
        except Exception as e:
            last_exc = e
            failures += 1
            if failures >= RETRY_MAX_ATTEMPTS:
                raise last_exc
            sleep_s = min(base_delay * failures * 2, RETRY_MAX_DELAY_SECONDS)
            log_info(f"⚠️ Wyjątek {type(e).__name__}: {e} — retry za {sleep_s:.1f}s (próba {failures}/{RETRY_MAX_ATTEMPTS})")
            await asyncio.sleep(sleep_s)


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
        # Zmienia się przy każdej zmianie Twojej pozycji w kolekcji (status,
        # ocena, komentarz, dane prywatne) — po niej poznajemy, co pobrać.
        "last_modified": _element_value(status_el, "lastmodified"),
        **extract_owned_version(item),
    }


def extract_owned_version(item: ET.Element) -> Dict[str, Any]:
    """Wersja posiadanego egzemplarza (`collection&version=1`).

    BGG oddaje ją jako `<version><item>` z nazwą wydania i linkami języków.
    Gdy wersja nie jest ustawiona w kolekcji, obu pól nie ma (None).
    """
    version_item = item.find("version/item")
    if version_item is None:
        return {"version_name": None, "version_languages": None}

    name = None
    for name_el in version_item.findall("name"):
        if name_el.attrib.get("type") == "primary":
            name = name_el.attrib.get("value")
            break
    if name is None:
        name = _element_value(version_item.find("name"))

    languages = [
        value
        for value in (
            link.attrib.get("value")
            for link in version_item.findall("link")
            if link.attrib.get("type") == "language"
        )
        if value
    ]
    return {
        "version_name": name or None,
        "version_languages": ", ".join(languages) or None,
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
        "publishers": [value for value in (l.attrib.get("value") for l in links if l.attrib.get("type") == "boardgamepublisher") if value],
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
# CO POBRAĆ: TYLKO NOWE, ZMIENIONE I PRZETERMINOWANE
# =============================================================================

def plan_game_fetches(
    basics: Dict[str, Dict[str, Any]],
    known: Dict[int, Dict[str, Any]],
    now: datetime,
    max_age_days: int = DETAILS_MAX_AGE_DAYS,
) -> Tuple[List[str], List[str]]:
    """Gry, dla których trzeba pobrać szczegóły (`thing`) i dane prywatne.

    - Szczegóły: nowe gry, gry bez szczegółów i szczegóły starsze niż
      `max_age_days`. Ocena, ranking, partie i statusy przychodzą z samej
      kolekcji, więc nie są powodem do pobierania szczegółów.
    - Dane prywatne (cena, data zakupu…): nowe gry i gry, których pozycja
      w kolekcji się zmieniła (`lastmodified`). Gdy w bazie nie ma jeszcze
      daty zmiany (pierwszy sync po wdrożeniu), ufamy temu, co już jest.
    """
    details: List[str] = []
    private: List[str] = []
    for bgg_id, basic in basics.items():
        row = known.get(int(bgg_id))
        if row is None:
            details.append(bgg_id)
            private.append(bgg_id)
            continue

        fetched_at = row.get("details_fetched_at")
        if fetched_at is None or (now - fetched_at).days >= max_age_days:
            details.append(bgg_id)

        stored = row.get("last_modified")
        current = basic.get("last_modified")
        if stored is not None and current != stored:
            private.append(bgg_id)
    return details, private


def chunks(ids: List[str], size: int = THING_BATCH_SIZE) -> List[List[str]]:
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def parse_thing_batch(root: ET.Element) -> Dict[str, Dict[str, Any]]:
    """Odpowiedź `thing` dla wielu id → szczegóły po id."""
    return {
        item.attrib["id"]: extract_details(item)
        for item in root.findall("item")
        if item.attrib.get("id")
    }


async def _load_known_games() -> Dict[int, Dict[str, Any]]:
    """Gry z bazy: data zmiany pozycji i kiedy pobrano szczegóły."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(BGGGame.bgg_id, BGGGame.last_modified, BGGGame.details_fetched_at)
        )
        return {
            row.bgg_id: {
                "last_modified": row.last_modified,
                "details_fetched_at": row.details_fetched_at,
            }
            for row in result.all()
        }


# =============================================================================
# DATA PERSISTENCE
# =============================================================================

def _changed_fields(model: Any, data: Dict[str, Any]) -> List[str]:
    return [key for key, value in data.items() if hasattr(model, key) and getattr(model, key) != value]


async def _persist_games(
    games_data: List[Dict[str, Any]],
    collection_ids: set[int],
) -> tuple[int, int, int, List[str], List[str], List[str]]:
    """Zapis do bazy. „Zaktualizowana" liczy się tylko, gdy coś się zmieniło —
    sync przepisuje dane z kolekcji wszystkim grom, ale większość zostaje
    bez zmian."""

    inserted = 0
    updated = 0
    deleted = 0
    inserted_titles: List[str] = []
    updated_titles: List[str] = []
    deleted_titles: List[str] = []

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
                changed = _changed_fields(model, data)
                if not changed:
                    continue
                apply_model_fields(model, data)
                # Sama data zmiany i data pobrania szczegółów to nie zmiana gry.
                if set(changed) - {"last_modified", "details_fetched_at"}:
                    log_info(f"♻️ {title}: {', '.join(sorted(changed))}")
                    updated += 1
                    updated_titles.append(title)
            else:
                session.add(BGGGame(**data))
                log_info(f"➕ Dodano nową grę: {title}")
                inserted += 1
                inserted_titles.append(title)

        result = await session.execute(select(BGGGame.bgg_id))
        all_db_ids = set(result.scalars().all())
        to_delete = all_db_ids - collection_ids
        if to_delete:
            result = await session.execute(select(BGGGame.bgg_id, BGGGame.title).where(BGGGame.bgg_id.in_(to_delete)))
            rows = result.all()
            deleted_titles.extend([row[1] or f"BGG ID {row[0]}" for row in rows])
            await session.execute(delete(BGGGame).where(BGGGame.bgg_id.in_(to_delete)))
            deleted = len(to_delete)

        await session.commit()
    finally:
        await session.close()

    return inserted, updated, deleted, inserted_titles, updated_titles, deleted_titles


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

STAGE_COUNT = 5  # kolekcja · szczegóły · dane prywatne · zapis · zakończenie


async def fetch_bgg_collection(username: str, ctx=None) -> None:
    if ctx is None:
        from app import jobs
        ctx = jobs.NULL_CTX

    log_info("📅 Rozpoczynam pobieranie kolekcji BGG")

    # version=1 — wersja posiadanego egzemplarza (nazwa wydania, języki).
    collection_url = f"{BGG_XML_BASE}/collection?username={username}&stats=1&version=1"
    start_time = datetime.utcnow()

    async with _make_client() as client:
        auth = BGGAuthSessionManager()

        # 1. Kolekcja — jedno zapytanie; BGG potrafi kazać czekać (202).
        ctx.set_stage("fetch_remote", detail=f"collection of {username}", index=1, count=STAGE_COUNT)
        collection_root = await fetch_xml(client, collection_url, on_wait=ctx.set_detail)
        collection_data = parse_collection_data(collection_root)
        collection_ids = {int(bgg_id) for bgg_id in collection_data.keys() if bgg_id is not None}
        basics = {
            bgg_id: extract_collection_basics(item)
            for bgg_id, item in collection_data.items()
            if bgg_id is not None
        }
        log_info(f"🔍 Kolekcja: {len(basics)} gier")

        known = await _load_known_games()
        now = datetime.utcnow()
        details_ids, private_ids = plan_game_fetches(basics, known, now)
        new_count = sum(1 for bgg_id in basics if int(bgg_id) not in known)
        log_info(
            f"🧮 Plan: szczegóły {len(details_ids)} (nowe {new_count}, starsze niż "
            f"{DETAILS_MAX_AGE_DAYS} dni lub bez szczegółów), dane prywatne {len(private_ids)} "
            f"(nowe i zmienione), reszta tylko dane z kolekcji"
        )
        ctx.set_counters(total=len(basics), details=len(details_ids), private=len(private_ids))

        # 2. Szczegóły paczkami po 20 — postęp po każdej paczce.
        details: Dict[str, Dict[str, Any]] = {}
        batches = chunks(details_ids)
        ctx.set_stage(
            "fetch_details", total=len(details_ids), unit="games",
            detail=f"{len(batches)} batches of {THING_BATCH_SIZE}", index=2, count=STAGE_COUNT,
        )
        for number, batch in enumerate(batches, start=1):
            if ctx.cancelled:
                log_info("⏹️ Przerwano na prośbę użytkownika (szczegóły)")
                return
            root = await fetch_xml(client, THING_BATCH_URL_TMPL.format(ids=",".join(batch)), on_wait=ctx.set_detail)
            parsed = parse_thing_batch(root)
            details.update(parsed)
            done = min(number * THING_BATCH_SIZE, len(details_ids))
            ctx.set_progress(done)
            ctx.set_detail(f"batch {number}/{len(batches)}")
            missing = len(batch) - len(parsed)
            log_info(
                f"📦 Szczegóły: paczka {number}/{len(batches)} — {len(parsed)} gier"
                + (f", {missing} bez odpowiedzi" if missing else "")
                + f" ({done}/{len(details_ids)})"
            )
            if number < len(batches):
                await asyncio.sleep(THING_REQUEST_PAUSE_SECONDS)

        # 3. Dane prywatne — jedno zapytanie na grę, tylko nowe i zmienione.
        private: Dict[str, Dict[str, Any]] = {}
        ctx.set_stage("fetch_private", total=len(private_ids), unit="games", index=3, count=STAGE_COUNT)
        for number, bgg_id in enumerate(private_ids, start=1):
            if ctx.cancelled:
                log_info("⏹️ Przerwano na prośbę użytkownika (dane prywatne)")
                return
            title = basics[bgg_id].get("title") or f"ID={bgg_id}"
            ctx.set_detail(title)
            data = await fetch_private_collection_item(client, auth, int(bgg_id))
            if data:
                private[bgg_id] = data
            ctx.set_progress(number)
            log_info(f"🔒 [{number}/{len(private_ids)}] {title} — {'dane prywatne' if data else 'brak danych prywatnych'}")
            if number < len(private_ids):
                await asyncio.sleep(PRIVATE_PAUSE_SECONDS)

        # 4. Zapis: dane z kolekcji dla wszystkich, szczegóły i dane prywatne
        #    tylko tam, gdzie je pobraliśmy (reszta zostaje w bazie).
        games_data: List[Dict[str, Any]] = []
        for bgg_id, basic in basics.items():
            data: Dict[str, Any] = {"bgg_id": int(bgg_id), **basic}
            if bgg_id in details:
                data.update(details[bgg_id])
                data["details_fetched_at"] = now
            elif int(bgg_id) not in known:
                log_info(f"⚠️ {basic.get('title') or bgg_id} (ID={bgg_id}) — brak szczegółów, zapisuję dane z kolekcji")
            if bgg_id in private:
                data.update(private[bgg_id])
            games_data.append(data)

        ctx.set_stage("db_sync", total=len(games_data), unit="games", index=4, count=STAGE_COUNT)
        inserted, updated, deleted, inserted_titles, updated_titles, deleted_titles = await _persist_games(
            games_data, collection_ids
        )
        ctx.set_progress(len(games_data))

    ctx.set_counters(
        # Rozmiar katalogu — bez niego podsumowanie dnia bez zmian nie miało
        # czego pokazać i kończyło się samym nagłówkiem „Stats".
        total=len(collection_data),
        inserted=inserted,
        updated=updated,
        removed=deleted,
        skipped=len(basics) - inserted - updated,
    )
    elapsed = (datetime.utcnow() - start_time).total_seconds()
    log_success(
        f"{ANSI_GREEN}🎉 Kolekcja BGG zsynchronizowana w {elapsed:.0f} s{ANSI_RESET} "
        f"(inserted={inserted}, updated={updated}, removed={deleted}) | "
        f"{ANSI_YELLOW}szczegóły={len(details)}/{len(details_ids)}, prywatne={len(private)}/{len(private_ids)}{ANSI_RESET}"
    )

    end_time = datetime.utcnow()
    stats = {
        "Total games": len(collection_data),
        "Added": inserted,
        "Updated": updated,
        "Removed": deleted,
        "Details fetched": len(details),
        "Private data fetched": len(private),
    }
    details_summary = {
        "Added games": inserted_titles,
        "Updated games": updated_titles,
        "Removed games": deleted_titles,
    }
    await send_scrape_message("BGG collection sync", "✅ SUCCESS", start_time, end_time, stats, details_summary)
