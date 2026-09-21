# app/utils/daily_summary.py
"""Dzienne podsumowanie przebiegów — jedna wiadomość na każdy sync.

Plik jest WSPÓLNY dla games-api / bgg-api / sleeves-api — trzymany jako kopia
w każdym repo, tak samo jak `app/jobs.py`. Różni się między repozytoriami tylko
słownikiem `SUMMARY_JOBS`.

Skąd dane: tabela `job_runs`, którą `app/jobs.py` i tak zapisuje po każdym
przebiegu. Nie ma tu żadnej nowej tabeli ani kolumny — bgg-api nie ma narzędzi
migracyjnych, więc schemat zostaje nietknięty.

Dlaczego osobna wiadomość na sync, a nie jedna zbiorcza: każdy sync ma inne
liczniki i inną historię, a w jednej wiadomości utonęłyby, nie mieszcząc się
przy okazji w limicie 4096 znaków Telegrama.

Uruchomienie z aplikacji (`trigger == "manual"`) omija blokadę powtórzeń —
skoro ktoś prosi o wiadomość, ma ją dostać, także po wieczornej wysyłce.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.database import AsyncSessionLocal
from app.utils.logging import log_error, log_info
from app.utils.telegram_notify import SERVICE_NAME, send_scrape_message

# Strefa ma znaczenie: podsumowanie idzie o 23:00 czasu polskiego, a doba musi
# się kończyć tam, gdzie kończy się dzień użytkownika, nie w UTC.
TIMEZONE = ZoneInfo("Europe/Warsaw")

# Godzina wysyłki, czas warszawski.
SUMMARY_HOUR = int(os.getenv("TELEGRAM_SUMMARY_HOUR", "23"))

# Wyłącznik na wypadek, gdyby podsumowania miały chwilowo zamilknąć.
SUMMARY_ENABLED = os.getenv("TELEGRAM_DAILY_SUMMARY", "true").lower() == "true"

# Zadania, które MAJĄ chodzić z harmonogramu — brak przebiegu to dla nich
# informacja, a nie cisza. Zadania spoza listy dostają wiadomość tylko wtedy,
# gdy faktycznie tego dnia przebiegły.
SUMMARY_JOBS: Dict[str, str] = {
    "bgg_collection": "BGG collection sync",
    "bgg_plays": "BGG plays sync",
    "bgg_accessories": "BGG accessories sync",
    "bgg_hotness_games": "BGG hotness games",
    "bgg_hotness_persons": "BGG hotness persons",
}

# Wiersze przepisane przy starcie procesu — redeploy nie jest awarią.
RESTART_ERROR = "przerwane: restart serwera"

# Zadania nazywają to samo różnie; mapa daje jedną etykietę na jedno pojęcie.
# Klucz spoza mapy jest pomijany, a nie zgadywany — lepiej nie pokazać liczby
# niż pokazać ją pod złą nazwą.
_ALIASES: List[Tuple[str, Tuple[str, ...]]] = [
    ("Dodane", ("added", "inserted")),
    ("Zaktualizowane", ("updated",)),
    ("Usunięte", ("removed", "deleted")),
    ("Pominięte", ("skipped", "unchanged")),
    ("Bez koszulek", ("no_sleeves",)),
    ("Oznaczone jako nieaktywne", ("marked_inactive",)),
    ("Pobrane okładki", ("covers_fetched",)),
    ("Przetworzone", ("processed_games", "games", "scanned")),
    ("Pozycji łącznie", ("total",)),
    ("Błędy", ("errors", "failed")),
]


def day_window(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """Początek dzisiejszej doby (czas lokalny) i chwila obecna."""
    current = (now or datetime.now(TIMEZONE)).astimezone(TIMEZONE)
    return current.replace(hour=0, minute=0, second=0, microsecond=0), current


async def _fetch_runs(since: datetime) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT job, state, trigger, counters, result, error, "
                "       started_at, finished_at "
                "FROM job_runs WHERE started_at >= :since ORDER BY started_at"
            ),
            {"since": since},
        )
        return [dict(row) for row in result.mappings()]


async def already_sent(within_hours: int = 12) -> bool:
    """Czy podsumowanie poszło niedawno.

    Zabezpiecza przed drugą wysyłką, gdy proces wstanie ponownie tuż po 23:00 —
    harmonogram żyje w procesie web i restartuje się przy każdym deployu.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT 1 FROM job_runs "
                "WHERE job = 'daily_summary' AND state = 'succeeded' "
                "  AND finished_at >= :since LIMIT 1"
            ),
            {"since": datetime.now(TIMEZONE) - timedelta(hours=within_hours)},
        )
        return result.first() is not None


def _sum_counters(runs: List[Dict[str, Any]]) -> Dict[str, int]:
    """Sumuje liczniki z całej doby, łącząc `counters` i `result`."""
    totals: Dict[str, int] = {}
    for run in runs:
        for source in (run.get("counters"), run.get("result")):
            if not isinstance(source, dict):
                continue
            for key, value in source.items():
                if isinstance(value, bool) or not isinstance(value, int):
                    continue
                totals[key] = totals.get(key, 0) + value
    return totals


def _stats_block(totals: Dict[str, int], errors: int) -> Dict[str, int]:
    stats: Dict[str, int] = {}
    for label, keys in _ALIASES:
        value = sum(totals.get(key, 0) for key in keys)
        if value:
            stats[label] = value
    if errors:
        stats["Nieudane przebiegi"] = errors
    return stats


def _plural(count: int, one: str, few: str, many: str) -> str:
    """Polska odmiana liczebnika: 1 przebieg, 2 przebiegi, 5 przebiegów."""
    if count == 1:
        return f"{count} {one}"
    last, last_two = count % 10, count % 100
    if 2 <= last <= 4 and not 12 <= last_two <= 14:
        return f"{count} {few}"
    return f"{count} {many}"


def _duration(run: Dict[str, Any]) -> Optional[timedelta]:
    started, finished = run.get("started_at"), run.get("finished_at")
    if not started or not finished:
        return None
    return finished - started


def build_note(runs: List[Dict[str, Any]]) -> str:
    """Zdanie o przebiegach: ile, ile udanych, restarty, ostatni i najdłuższy."""
    restarts = [r for r in runs if (r.get("error") or "") == RESTART_ERROR]
    real = [r for r in runs if (r.get("error") or "") != RESTART_ERROR]
    failed = [r for r in real if r.get("state") == "failed"]

    parts = [_plural(len(real), "przebieg", "przebiegi", "przebiegów")]
    if failed:
        parts.append(_plural(len(real) - len(failed), "udany", "udane", "udanych"))
        parts.append(_plural(len(failed), "nieudany", "nieudane", "nieudanych"))
    else:
        parts.append("wszystkie udane")
    if restarts:
        parts.append(
            _plural(len(restarts), "restart", "restarty", "restartów") + " serwera"
        )

    lines = [" · ".join(parts)]

    if real:
        last = real[-1]
        when = last.get("finished_at") or last.get("started_at")
        state = "udany" if last.get("state") == "succeeded" else "nieudany"
        extra = []
        if when:
            extra.append(f"Ostatni: {when.astimezone(TIMEZONE).strftime('%H:%M')} ({state})")
        longest = max((d for d in (_duration(r) for r in real) if d), default=None)
        if longest:
            extra.append(f"najdłuższy: {str(longest).split('.')[0]}")
        if extra:
            lines.append(" · ".join(extra))

    if failed:
        last_error = (failed[-1].get("error") or "").replace("`", "'")
        if last_error:
            lines.append(f"Ostatni błąd: {last_error[:200]}")

    return "\n".join(lines)


async def run_daily_summary(ctx=None) -> Dict[str, Any]:
    """Wysyła po jednej wiadomości na sync. Bezpieczne do powtórzenia."""
    if not SUMMARY_ENABLED:
        log_info("ℹ️ Podsumowanie dnia wyłączone (TELEGRAM_DAILY_SUMMARY).")
        return {"status": "disabled"}

    # Blokada chroni przed DRUGĄ wysyłką po restarcie procesu, a nie przed
    # świadomym tapnięciem w aplikacji. Ręczne uruchomienie ma wysłać zawsze —
    # inaczej przycisk po 23:00 wyglądałby na zepsuty.
    manual = getattr(ctx, "trigger", None) == "manual"

    if manual:
        log_info("▶️ Podsumowanie dnia na żądanie — pomijam blokadę powtórzeń.")
    elif await already_sent():
        log_info("ℹ️ Podsumowanie dnia już poszło — pomijam.")
        return {"status": "skipped", "reason": "already_sent"}

    since, until = day_window()
    runs = await _fetch_runs(since)

    by_job: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        if run["job"] == "daily_summary":
            continue
        by_job.setdefault(run["job"], []).append(run)

    names = dict(SUMMARY_JOBS)
    for job in by_job:
        names.setdefault(job, job)

    sent = 0
    for job, label in names.items():
        job_runs = by_job.get(job, [])

        if not job_runs:
            if job not in SUMMARY_JOBS:
                continue
            # Zadanie z harmonogramu, które nie ruszyło ani razu — cisza
            # harmonogramu jest groźniejsza niż awaria, więc mówimy o niej wprost.
            await send_scrape_message(
                label, "⚠️ BRAK PRZEBIEGÓW", since, until, {}, {},
                notes="Zadanie nie uruchomiło się ani razu w ciągu doby.",
                severity="summary",
            )
            sent += 1
            continue

        real = [r for r in job_runs if (r.get("error") or "") != RESTART_ERROR]
        failed = sum(1 for r in real if r.get("state") == "failed")
        stats = _stats_block(_sum_counters(real), failed)

        await send_scrape_message(
            label,
            "📊 PODSUMOWANIE DNIA" if not failed else "📊 PODSUMOWANIE DNIA (z błędami)",
            since,
            until,
            stats,
            {},
            notes=build_note(job_runs),
            severity="summary",
        )
        sent += 1

    log_info(f"📊 {SERVICE_NAME}: wysłano {sent} podsumowań dnia.")
    return {"status": "ok", "messages": sent, "jobs": len(names)}


async def schedule_entry() -> None:
    """Wejście z harmonogramu — przez rejestr, żeby przebieg był widoczny."""
    from app import jobs

    try:
        await jobs.start("daily_summary", trigger="schedule")
    except Exception as exc:  # noqa: BLE001 — harmonogram nie może paść na wysyłce
        log_error(f"⚠️ Nie udało się uruchomić podsumowania dnia: {exc}")
