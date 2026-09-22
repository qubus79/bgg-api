"""Pobieranie rozgrywek jednym ciągiem — po użytkowniku, nie po grach.

Dotychczasowa ścieżka (`bgg_plays.py`) pyta BGG o każdą grę z kolekcji osobno:
433 zapytania i 433 pauzy po 1,2 s, czyli 8 min 40 s samego czekania. Ten
moduł robi to samo jednym dziennikiem: `xmlapi2/plays?username=…` zwraca
WSZYSTKIE rozgrywki użytkownika, po 100 na stronę, z `total` w korzeniu.

Dzięki temu każdy przebieg jest pełnym przebiegiem — a to jedyny sposób, żeby
zobaczyć komentarz dopisany do starej partii. BGG nie ma filtra „zmienione po";
`mindate`/`maxdate` filtrują datę rozgrywki, nie datę edycji. Skoro edycji nie
da się wyszukać, trzeba pobierać komplet — więc komplet musi być tani.

Model `BGGPlay` zostaje bez zmian. XML nie niesie trzech pól, które niósł
prywatny `geekplay.php` (`tstamp`, `length_ms`, `online`); przy aktualizacji
nie są ruszane, więc wiersze zapisane wcześniej zachowują swoje wartości.
"""

import os
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, cast

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.bgg_plays import BGGPlay
from app.scraper.bgg_game import fetch_xml
from app.services.bgg.auth_session import BGGAuthSessionManager
from app.utils.convert import to_bool, to_int
from app.utils.logging import log_error, log_info, log_success
from app.utils.telegram_notify import send_scrape_message


BGG_XML_BASE = "https://boardgamegeek.com/xmlapi2"
USER_AGENT = os.getenv("USER_AGENT", "bgg-api/1.0 (+https://railway.app)")

# Ile rozgrywek BGG oddaje na jednej stronie. To stała serwisu, nie nasz wybór.
PAGE_SIZE = 100
# Zabezpieczenie przed pętlą bez końca, gdyby `total` skłamał.
MAX_PAGES = int(os.getenv("BGG_PLAYS_MAX_PAGES", "200"))

# Pola, których ten endpoint nie zna. Przy aktualizacji istniejącego wiersza
# zostawiamy w nich to, co już tam jest — inaczej przejście na nowe źródło
# skasowałoby dane zebrane starym.
FIELDS_XML_DOES_NOT_KNOW = ("tstamp", "length_ms", "online")

# Pola, które z XML-a wyliczamy, ale nie zawsze się da (rozgrywka bez zapisanej
# listy graczy). Pustką nie nadpisujemy tego, co już w bazie jest.
PRESERVE_IF_MISSING = ("num_players", "win_state")

# Usuwanie rozgrywek skasowanych w BGG. Dotąd niemożliwe — pytając o grę naraz,
# nigdy nie mieliśmy kompletu. Próg bezpieczeństwa niżej broni przed wyczyszczeniem
# dziennika, gdy BGG odda niepełną odpowiedź.
PRUNE_DELETED = os.getenv("BGG_PLAYS_PRUNE_DELETED", "1") not in ("0", "false", "False")
MIN_KEEP_RATIO = float(os.getenv("BGG_PLAYS_MIN_KEEP_RATIO", "0.8"))


def apply_to_row(row: Any, data: Dict[str, Any]) -> bool:
    """Przepisuje dane na wiersz. Zwraca, czy cokolwiek się zmieniło.

    Wydzielone z zapisu, żeby regułę zachowywania pól dało się sprawdzić bez bazy.
    """
    changed = False
    for key, value in data.items():
        # Pola spoza tego źródła zostawiamy nietknięte — inaczej przejście na
        # nowy endpoint wyzerowałoby `tstamp`, po którym aplikacja sortuje
        # rozgrywki z tego samego dnia.
        if key in FIELDS_XML_DOES_NOT_KNOW:
            continue
        if value is None and key in PRESERVE_IF_MISSING:
            continue
        if getattr(row, key) != value:
            setattr(row, key, value)
            changed = True
    return changed


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        http2=True,
        timeout=httpx.Timeout(30.0),
    )


# =============================================================================
# PARSOWANIE XML
# =============================================================================

def _players(play_el: ET.Element) -> List[Dict[str, Any]]:
    """Gracze w kształcie, jakiego oczekuje aplikacja.

    `BGGPlaysRemoteData.BGGPlayPlayer` czyta `name`, `username`, `score`, `win`,
    `new` jako łańcuchy i buduje identyfikator z `uplayerid`/`playerid`. XML ma
    zamiast nich `userid`, więc przepisujemy go pod `uplayerid` — bez tego
    identyfikator spadłby do nazwy gracza.
    """
    out: List[Dict[str, Any]] = []
    container = play_el.find("players")
    if container is None:
        return out
    for el in container.findall("player"):
        userid = (el.get("userid") or "").strip()
        out.append({
            "name": el.get("name") or "",
            "username": el.get("username") or None,
            "score": el.get("score"),
            "win": el.get("win"),
            "new": el.get("new"),
            "rating": el.get("rating"),
            "color": el.get("color"),
            "startposition": el.get("startposition"),
            # Aplikacja szuka najpierw `uplayerid`, potem `playerid`.
            "uplayerid": userid if userid and userid != "0" else None,
            "userid": userid or None,
        })
    return out


def _subtypes(item_el: Optional[ET.Element]) -> List[Dict[str, Any]]:
    if item_el is None:
        return []
    container = item_el.find("subtypes")
    if container is None:
        return []
    return [{"subtype": el.get("value")} for el in container.findall("subtype") if el.get("value")]


def _win_state(players: List[Dict[str, Any]], username: Optional[str]) -> Optional[str]:
    """Odtwarza `win_state`, którego XML nie podaje wprost.

    Znaczenie zostaje to samo co w starym źródle: czy TEN użytkownik wygrał.
    """
    if not players:
        return None
    if username:
        mine = [p for p in players if (p.get("username") or "").lower() == username.lower()]
        if mine:
            return "1" if any(p.get("win") == "1" for p in mine) else "0"
    return None


def _play_to_model_data(
    play_el: ET.Element, *, user_id: Optional[int], username: Optional[str]
) -> Optional[Dict[str, Any]]:
    play_id = to_int(play_el.get("id"))
    if not play_id:
        return None

    item_el = play_el.find("item")
    object_id = to_int(item_el.get("objectid")) if item_el is not None else None
    if not object_id:
        # Rozgrywka bez gry to nic, czego dałoby się użyć — `object_id` jest NOT NULL.
        return None

    players = _players(play_el)
    comments_el = play_el.find("comments")
    comments = (comments_el.text or "").strip() if comments_el is not None else None
    comments = comments or None

    data: Dict[str, Any] = {
        "play_id": play_id,
        "object_id": object_id,
        "object_type": (item_el.get("objecttype") if item_el is not None else None) or "thing",
        "user_id": user_id,
        "username": username,
        "play_date": play_el.get("date"),
        "quantity": to_int(play_el.get("quantity")),
        "length": to_int(play_el.get("length")),
        "location": play_el.get("location") or None,
        "num_players": len(players) or None,
        "comments_value": comments,
        # Stare źródło dawało tu wersję z HTML-em. Nic jej nie wyświetla
        # (aplikacja czyta `comments_value`), więc trzymamy ten sam tekst,
        # żeby kolumna nie zrobiła się nagle pusta.
        "comments_rendered": comments,
        "incomplete": to_bool(play_el.get("incomplete")),
        "now_in_stats": to_bool(play_el.get("nowinstats")),
        "win_state": _win_state(players, username),
        "game_name": (item_el.get("name") if item_el is not None else None),
        "players": players,
        "subtypes": _subtypes(item_el),
    }
    data["raw"] = dict(data)
    return data


# =============================================================================
# POBIERANIE
# =============================================================================

async def _fetch_page(client: httpx.AsyncClient, username: str, page: int) -> ET.Element:
    url = f"{BGG_XML_BASE}/plays?username={username}&page={page}"
    # `fetch_xml` zna 202 (BGG kolejkuje eksport), 429 i 5xx, i sam robi pauzę
    # po udanej odpowiedzi — to dokładnie ten endpoint tego wymaga.
    return await fetch_xml(client, url)


async def _fetch_all_plays(client: httpx.AsyncClient, username: str, ctx) -> Tuple[List[Dict[str, Any]], int, bool]:
    """Zwraca (rozgrywki, ile BGG deklaruje, czy przebieg był kompletny)."""
    collected: List[Dict[str, Any]] = []
    declared_total = 0
    user_id: Optional[int] = None
    full_pass = True
    page = 1

    while page <= MAX_PAGES:
        root = await _fetch_page(client, username, page)

        if page == 1:
            declared_total = to_int(root.get("total")) or 0
            user_id = to_int(root.get("userid"))
            pages = max(1, -(-declared_total // PAGE_SIZE)) if declared_total else 1
            ctx.set_stage("fetch_remote", total=declared_total or None, unit="plays", index=1, count=2)
            log_info(f"📖 Dziennik {username}: {declared_total} rozgrywek, {pages} stron po {PAGE_SIZE}.")

        elements = root.findall("play")
        if not elements:
            break

        for el in elements:
            data = _play_to_model_data(el, user_id=user_id, username=username)
            if data is None:
                continue
            collected.append(data)

        ctx.set_progress(len(collected))
        ctx.set_detail(f"strona {page}")

        if len(elements) < PAGE_SIZE:
            break
        page += 1

    if page > MAX_PAGES:
        # Nie wiemy, ile zostało — komplet jest wtedy fikcją i usuwanie odpada.
        full_pass = False
        log_error(f"⚠️ Przerwano na {MAX_PAGES} stronach; dziennik może być niepełny.")

    if declared_total and len(collected) < declared_total:
        full_pass = False
        log_error(
            f"⚠️ Pobrano {len(collected)} z {declared_total} deklarowanych rozgrywek — "
            "traktuję przebieg jako niepełny."
        )

    return collected, declared_total, full_pass


# =============================================================================
# ZAPIS
# =============================================================================

async def _persist(plays: List[Dict[str, Any]], ctx) -> Dict[str, Any]:
    """Zapisuje komplet jednym przejściem: jedno zapytanie po stan, potem zmiany.

    Stara ścieżka otwierała sesję na grę i robiła `SELECT` na rozgrywkę. Mając
    całość w ręku wystarczy raz odczytać, co już jest.
    """
    inserted = 0
    updated = 0
    unchanged = 0
    inserted_titles: List[str] = []
    updated_titles: List[str] = []

    ctx.set_stage("db_sync", total=len(plays), unit="plays", index=2, count=2)

    session = cast(AsyncSession, AsyncSessionLocal())
    try:
        async with session.begin():
            existing_rows = (await session.execute(select(BGGPlay))).scalars().all()
            existing = {row.play_id: row for row in existing_rows}

            for index, data in enumerate(plays, start=1):
                row = existing.get(data["play_id"])
                if row is None:
                    session.add(BGGPlay(**data))
                    inserted += 1
                    if data.get("game_name"):
                        inserted_titles.append(data["game_name"])
                else:
                    if apply_to_row(row, data):
                        updated += 1
                        if data.get("game_name"):
                            updated_titles.append(data["game_name"])
                    else:
                        unchanged += 1

                if index % 50 == 0:
                    ctx.set_progress(index)
                    ctx.set_counters(inserted=inserted, updated=updated, skipped=unchanged)

            ctx.set_progress(len(plays))
    finally:
        await session.close()

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "inserted_titles": sorted(set(inserted_titles)),
        "updated_titles": sorted(set(updated_titles)),
    }


async def _prune_deleted(seen_ids: set[int], full_pass: bool) -> Tuple[int, str]:
    """Usuwa rozgrywki, których w BGG już nie ma. Zwraca (ile, powód pominięcia)."""
    if not PRUNE_DELETED:
        return 0, "wyłączone ustawieniem"
    if not full_pass:
        return 0, "przebieg niepełny"

    session = cast(AsyncSession, AsyncSessionLocal())
    try:
        async with session.begin():
            rows = (await session.execute(select(BGGPlay.id, BGGPlay.play_id))).all()
            total_existing = len(rows)
            if total_existing and len(seen_ids) < total_existing * MIN_KEEP_RATIO:
                # BGG oddało podejrzanie mało. Wolimy zostawić nadmiar niż wyciąć
                # dziennik, bo jednej odpowiedzi zabrakło.
                return 0, (
                    f"pobrano {len(seen_ids)} przy {total_existing} w bazie — "
                    f"poniżej progu {MIN_KEEP_RATIO:.0%}"
                )
            stale = [row_id for row_id, play_id in rows if play_id not in seen_ids]
            if not stale:
                return 0, ""
            for row in (await session.execute(select(BGGPlay).where(BGGPlay.id.in_(stale)))).scalars():
                await session.delete(row)
            return len(stale), ""
    finally:
        await session.close()


# =============================================================================
# WEJŚCIE
# =============================================================================

async def update_bgg_plays_for_user(username: str, ctx=None) -> Dict[str, Any]:
    if ctx is None:
        from app import jobs
        ctx = jobs.NULL_CTX

    log_info(f"📅 Pobieram dziennik rozgrywek BGG użytkownika {username}")
    start_time = datetime.utcnow()

    async with _make_client() as client:
        # Dziennik jest publiczny, ale ciasteczka nic nie kosztują i zdejmują
        # pytanie, czy BGG czegoś nie chowa przed niezalogowanym.
        try:
            await BGGAuthSessionManager().ensure_session(client)
        except Exception as exc:  # noqa: BLE001 — brak logowania nie może przerwać pobierania
            log_info(f"ℹ️ Bez sesji BGG ({type(exc).__name__}); dziennik publiczny i tak się pobierze.")

        plays, declared_total, full_pass = await _fetch_all_plays(client, username, ctx)

    result = await _persist(plays, ctx)
    seen_ids = {p["play_id"] for p in plays}
    removed, prune_note = await _prune_deleted(seen_ids, full_pass)

    ctx.set_counters(
        total=len(plays),
        inserted=result["inserted"],
        updated=result["updated"],
        skipped=result["unchanged"],
        removed=removed,
    )

    log_success(
        f"✅ Dziennik zsynchronizowany. Pobrane: {len(plays)}/{declared_total or '?'}, "
        f"nowe: {result['inserted']}, zmienione: {result['updated']}, "
        f"bez zmian: {result['unchanged']}, usunięte: {removed}"
    )

    end_time = datetime.utcnow()
    stats = {
        "Plays in BGG log": len(plays),
        "New plays": result["inserted"],
        "Updated plays": result["updated"],
        "Unchanged plays": result["unchanged"],
        "Removed plays": removed,
    }
    details: Dict[str, List[str]] = {}
    if result["inserted_titles"]:
        details["New plays"] = result["inserted_titles"]
    if result["updated_titles"]:
        details["Updated plays"] = result["updated_titles"]

    notes = None
    if not full_pass:
        notes = "Przebieg niepełny — pominąłem usuwanie rozgrywek."
    elif prune_note:
        notes = f"Usuwanie pominięte: {prune_note}"

    await send_scrape_message(
        "BGG plays sync",
        "⚠️ SUCCESS (niepełny)" if not full_pass else "✅ SUCCESS",
        start_time,
        end_time,
        stats,
        details,
        notes=notes,
        severity="error" if not full_pass else "success",
    )

    return {
        "total": len(plays),
        "inserted": result["inserted"],
        "updated": result["updated"],
        "skipped": result["unchanged"],
        "removed": removed,
    }
