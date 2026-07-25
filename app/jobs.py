# app/jobs.py
"""Rejestr długotrwałych zadań (scrape/update) ze śledzeniem postępu.

Plik jest WSPÓLNY dla games-api / bgg-api / sleeves-api — trzymany jako kopia
w każdym repo (brak infrastruktury pakietowej). Zależy wyłącznie od
`app.database.AsyncSessionLocal` oraz `app.utils.logging.{log_info, log_error}`,
które istnieją w każdym z tych repo.

Idea:
- `register(name, fn)` — rejestruje funkcję aktualizującą,
- `start(name)` — uruchamia ją w tle i NATYCHMIAST wraca (klient nie czeka),
- funkcja dostaje `ctx: JobContext` i raportuje etap/postęp/liczniki
  synchronicznymi metodami (bez await w gorących pętlach),
- `status(name)` / `status_all()` — pełny obraz: etap, ile z ilu, %, ETA,
  liczniki na żywo, wynik lub błąd.

Stan żyje w pamięci (jeden worker uvicorn), a dodatkowo jest zapisywany do
tabeli `job_runs`, żeby przetrwać redeploy Railway.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import text

from app.database import AsyncSessionLocal
from app.utils.logging import log_error, log_info

# Etykiety etapów — serwer wysyła gotowy tekst, aplikacja renderuje go dosłownie,
# więc nowy etap nie wymaga wydania nowej wersji aplikacji.
STAGE_LABELS: dict[str, str] = {
    "queued": "W kolejce",
    "starting": "Rozpoczynanie",
    "fetch_catalogue": "Pobieranie katalogu",
    "fetch_details": "Pobieranie szczegółów gier",
    "fetch_remote": "Pobieranie danych z serwisu",
    "db_sync": "Zapis do bazy",
    "finalizing": "Finalizowanie",
    "done": "Zakończono",
}

FLUSH_INTERVAL_SECONDS = 5.0
KEEP_RUNS_PER_JOB = 20

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS job_runs (
    id SERIAL PRIMARY KEY,
    job VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL UNIQUE,
    state VARCHAR NOT NULL,
    trigger VARCHAR,
    stage VARCHAR,
    stage_detail VARCHAR,
    stage_index INTEGER,
    stage_count INTEGER,
    progress_current INTEGER,
    progress_total INTEGER,
    progress_unit VARCHAR,
    counters JSONB,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    result JSONB,
    error TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
)
"""

_CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_job_runs_job_started ON job_runs (job, started_at DESC)"
)

_UPSERT_SQL = """
INSERT INTO job_runs (
    job, run_id, state, trigger, stage, stage_detail, stage_index, stage_count,
    progress_current, progress_total, progress_unit, counters,
    started_at, finished_at, result, error, updated_at
) VALUES (
    :job, :run_id, :state, :trigger, :stage, :stage_detail, :stage_index, :stage_count,
    :progress_current, :progress_total, :progress_unit, CAST(:counters AS JSONB),
    :started_at, :finished_at, CAST(:result AS JSONB), :error, now()
)
ON CONFLICT (run_id) DO UPDATE SET
    state = EXCLUDED.state,
    stage = EXCLUDED.stage,
    stage_detail = EXCLUDED.stage_detail,
    stage_index = EXCLUDED.stage_index,
    stage_count = EXCLUDED.stage_count,
    progress_current = EXCLUDED.progress_current,
    progress_total = EXCLUDED.progress_total,
    progress_unit = EXCLUDED.progress_unit,
    counters = EXCLUDED.counters,
    finished_at = EXCLUDED.finished_at,
    result = EXCLUDED.result,
    error = EXCLUDED.error,
    updated_at = now()
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass
class JobRecord:
    """Stan pojedynczego zadania (źródło prawdy trzymane w pamięci procesu)."""

    job: str
    label: str
    state: str = "idle"          # idle | running | succeeded | failed
    run_id: str | None = None
    trigger: str | None = None   # manual | schedule
    stage: str | None = None
    stage_detail: str | None = None
    stage_index: int | None = None
    stage_count: int | None = None
    current: int | None = None
    total: int | None = None
    unit: str | None = None
    counters: dict[str, int] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict | None = None
    error: str | None = None
    last_success_at: datetime | None = None
    persist: bool = True
    cancel_requested: bool = False
    dirty: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None


class JobContext:
    """Uchwyt przekazywany do funkcji aktualizującej.

    WSZYSTKIE metody są synchroniczne — można je wołać w środku ciasnych pętli
    i callbacków `asyncio.gather` bez zamieniania funkcji na async i bez
    dokładania awaitów w gorących ścieżkach.
    """

    def __init__(self, record: JobRecord | None) -> None:
        self._record = record

    def set_stage(
        self,
        stage: str,
        *,
        total: int | None = None,
        unit: str | None = None,
        detail: str | None = None,
        index: int | None = None,
        count: int | None = None,
    ) -> None:
        record = self._record
        if record is None:
            return
        record.stage = stage
        record.stage_detail = detail
        record.current = 0
        record.total = total
        record.unit = unit
        if index is not None:
            record.stage_index = index
        if count is not None:
            record.stage_count = count
        record.dirty = True
        log_info(f"[job:{record.job}] etap: {STAGE_LABELS.get(stage, stage)}"
                 + (f" (0/{total} {unit or ''})" if total else ""))

    def set_progress(self, current: int, total: int | None = None) -> None:
        record = self._record
        if record is None:
            return
        record.current = current
        if total is not None:
            record.total = total
        record.dirty = True

    def bump(self, n: int = 1) -> None:
        record = self._record
        if record is None:
            return
        record.current = (record.current or 0) + n
        record.dirty = True

    def set_detail(self, text_value: str | None) -> None:
        record = self._record
        if record is None:
            return
        record.stage_detail = text_value
        record.dirty = True

    def set_counters(self, **values: int) -> None:
        """Nadpisuje liczniki na żywo, np. set_counters(added=3, updated=12)."""
        record = self._record
        if record is None:
            return
        record.counters.update({k: v for k, v in values.items() if v is not None})
        record.dirty = True

    def add_counter(self, name: str, n: int = 1) -> None:
        record = self._record
        if record is None:
            return
        record.counters[name] = record.counters.get(name, 0) + n
        record.dirty = True

    @property
    def cancelled(self) -> bool:
        record = self._record
        return bool(record and record.cancel_requested)


NULL_CTX = JobContext(None)

_JOBS: dict[str, JobRecord] = {}
_FUNCS: dict[str, Callable[..., Any]] = {}
_LAST_UPDATE_FNS: dict[str, Callable[[], Any]] = {}


def register(
    name: str,
    fn: Callable[..., Any],
    *,
    label: str | None = None,
    last_update_fn: Callable[[], Any] | None = None,
    stage_count: int | None = None,
    persist: bool = True,
) -> None:
    """Rejestruje zadanie. `fn` może (ale nie musi) przyjmować argument `ctx`."""
    record = _JOBS.get(name)
    if record is None:
        record = JobRecord(job=name, label=label or name)
        _JOBS[name] = record
    record.label = label or record.label
    record.persist = persist
    if stage_count is not None:
        record.stage_count = stage_count
    _FUNCS[name] = fn
    if last_update_fn is not None:
        _LAST_UPDATE_FNS[name] = last_update_fn


def known_jobs() -> list[str]:
    return list(_JOBS.keys())


async def _call(fn: Callable[..., Any], ctx: JobContext, kwargs: dict) -> Any:
    """Woła funkcję aktualizującą, przekazując `ctx` tylko gdy go przyjmuje.

    Dzięki temu można rejestrować funkcje jeszcze nieoinstrumentowane —
    zadziałają, po prostu bez raportowania postępu.
    """
    try:
        accepts_ctx = "ctx" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        accepts_ctx = False
    if accepts_ctx:
        return await fn(ctx=ctx, **kwargs)
    return await fn(**kwargs)


async def start(name: str, trigger: str = "manual", **kwargs: Any) -> tuple[JobRecord, bool]:
    """Uruchamia zadanie w tle. Zwraca (rekord, czy_przyjęto).

    Gdy zadanie już biegnie → (rekord trwającego biegu, False); nie startuje
    drugiego. Sprawdzenie i ustawienie stanu jest synchroniczne (bez await
    pomiędzy), więc na jednowątkowej pętli zdarzeń jest atomowe.
    """
    record = _JOBS.get(name)
    if record is None:
        raise KeyError(name)

    if record.state == "running":
        return record, False

    record.state = "running"
    record.run_id = uuid.uuid4().hex
    record.trigger = trigger
    record.started_at = _utcnow()
    record.finished_at = None
    record.stage = "queued"
    record.stage_detail = None
    record.stage_index = None
    record.current = None
    record.total = None
    record.unit = None
    record.counters = {}
    record.result = None
    record.error = None
    record.cancel_requested = False
    record.dirty = True

    record.task = asyncio.create_task(_runner(name, record, kwargs))
    log_info(f"▶️ Job '{name}' wystartował (trigger={trigger}, run_id={record.run_id})")
    return record, True


async def run_foreground(name: str, trigger: str = "manual", **kwargs: Any) -> dict:
    """Uruchamia zadanie i CZEKA na wynik — dla starych, synchronicznych endpointów.

    Dzięki temu stare ścieżki zachowują dotychczasowe zachowanie i odpowiedź,
    a jednocześnie współdzielą blokadę z nowym mechanizmem (brak równoległych
    scrape'ów).
    """
    record, accepted = await start(name, trigger=trigger, **kwargs)
    if not accepted:
        return {"status": "already_running", "job": name, "run_id": record.run_id}
    if record.task is not None:
        await asyncio.shield(record.task)
    if record.state == "succeeded":
        return record.result or {"status": "ok"}
    return {"status": "failed", "error": record.error}


async def request_cancel(name: str) -> JobRecord:
    """Kooperatywne anulowanie — funkcja sama sprawdza `ctx.cancelled`."""
    record = _JOBS.get(name)
    if record is None:
        raise KeyError(name)
    if record.state == "running":
        record.cancel_requested = True
        record.dirty = True
        log_info(f"⛔ Zgłoszono anulowanie job '{name}'")
    return record


async def _runner(name: str, record: JobRecord, kwargs: dict) -> None:
    ctx = JobContext(record)
    flusher = asyncio.create_task(_flush_loop(record))
    try:
        async with record.lock:
            ctx.set_stage("starting")
            result = await _call(_FUNCS[name], ctx, kwargs)
        if isinstance(result, dict):
            record.result = result
        elif result is not None:
            record.result = {"result": str(result)}
        else:
            record.result = {"status": "ok"}
        record.state = "succeeded"
        record.last_success_at = _utcnow()
        record.stage = "done"
        record.stage_detail = None
        log_info(f"✅ Job '{name}' zakończony: {record.result}")
    except asyncio.CancelledError:
        record.state = "failed"
        record.error = "anulowano"
        log_error(f"⛔ Job '{name}' anulowany")
    except Exception as exc:  # noqa: BLE001 — awaria zawsze ma wylądować jako 'failed'
        record.state = "failed"
        record.error = f"{type(exc).__name__}: {exc}"
        log_error(f"❌ Job '{name}' nie powiódł się: {record.error}")
    finally:
        record.finished_at = _utcnow()
        record.dirty = True
        flusher.cancel()
        await _flush(record)
        await _prune(name)


# --- Serializacja -----------------------------------------------------------

def _serialize(record: JobRecord) -> dict:
    """Buduje JobStatus. Pola pochodne (%, tempo, ETA) liczone są TUTAJ,
    a nie trzymane w rekordzie — zero kosztu w pętli, zawsze aktualne."""
    now = _utcnow()

    elapsed = None
    if record.started_at:
        end = record.finished_at or now
        elapsed = round(max(0.0, (end - record.started_at).total_seconds()), 1)

    percent = None
    if record.total and record.total > 0 and record.current is not None:
        percent = round(min(100.0, record.current / record.total * 100), 1)

    rate = None
    eta = None
    if record.state == "running" and elapsed and elapsed > 1 and record.current:
        rate = round(record.current / elapsed, 2)
        if rate > 0 and record.total and record.total > record.current:
            eta = int((record.total - record.current) / rate)

    progress = None
    if record.current is not None or record.total is not None:
        progress = {
            "current": record.current or 0,
            "total": record.total,
            "unit": record.unit,
            "percent": percent,
        }

    return {
        "job": record.job,
        "label": record.label,
        "state": record.state,
        "run_id": record.run_id,
        "trigger": record.trigger,
        "stage": record.stage,
        "stage_label": STAGE_LABELS.get(record.stage or "", record.stage),
        "stage_detail": record.stage_detail,
        "stage_index": record.stage_index,
        "stage_count": record.stage_count,
        "progress": progress,
        "counters": record.counters or None,
        "elapsed_seconds": elapsed,
        "rate_per_second": rate,
        "eta_seconds": eta,
        "started_at": _iso(record.started_at),
        "finished_at": _iso(record.finished_at),
        "duration_seconds": elapsed if record.finished_at else None,
        "result": record.result,
        "error": record.error,
        "last_success_at": _iso(record.last_success_at),
        "cancel_requested": record.cancel_requested,
    }


async def _data_last_update(name: str) -> str | None:
    fn = _LAST_UPDATE_FNS.get(name)
    if fn is None:
        return None
    try:
        value = await fn()
        if isinstance(value, dict):
            value = value.get("last_update")
        if value in (None, "", "n/a"):
            return None
        return str(value)
    except Exception as exc:  # noqa: BLE001 — świeżość danych to dodatek, nie może wywalić statusu
        log_error(f"⚠️ Nie udało się odczytać last_update dla '{name}': {exc}")
        return None


async def status(name: str) -> dict:
    record = _JOBS.get(name)
    if record is None:
        raise KeyError(name)
    data = _serialize(record)
    data["data_last_update"] = await _data_last_update(name)
    return data


async def status_all() -> list[dict]:
    out = []
    for name in _JOBS:
        out.append(await status(name))
    return out


# --- Trwałość ---------------------------------------------------------------

async def _flush_loop(record: JobRecord) -> None:
    """Zapisuje stan do bazy najwyżej co FLUSH_INTERVAL_SECONDS (nie co grę)."""
    try:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            if record.dirty:
                await _flush(record)
    except asyncio.CancelledError:
        return


async def _flush(record: JobRecord) -> None:
    if not record.persist or not record.run_id:
        record.dirty = False
        return
    record.dirty = False
    params = {
        "job": record.job,
        "run_id": record.run_id,
        "state": record.state,
        "trigger": record.trigger,
        "stage": record.stage,
        "stage_detail": (record.stage_detail or "")[:500] or None,
        "stage_index": record.stage_index,
        "stage_count": record.stage_count,
        "progress_current": record.current,
        "progress_total": record.total,
        "progress_unit": record.unit,
        "counters": json.dumps(record.counters or {}, default=str),
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "result": json.dumps(record.result, default=str) if record.result is not None else None,
        "error": record.error,
    }
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text(_UPSERT_SQL), params)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — zapis stanu nie może przerwać samego zadania
        log_error(f"⚠️ Nie udało się zapisać stanu job '{record.job}': {exc}")


async def _prune(name: str) -> None:
    record = _JOBS.get(name)
    if record is None or not record.persist:
        return
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    "DELETE FROM job_runs WHERE job = :job AND id NOT IN ("
                    "  SELECT id FROM job_runs WHERE job = :job "
                    "  ORDER BY started_at DESC NULLS LAST LIMIT :keep)"
                ),
                {"job": name, "keep": KEEP_RUNS_PER_JOB},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log_error(f"⚠️ Nie udało się wyczyścić historii job '{name}': {exc}")


async def init_jobs_table() -> None:
    """Tworzy tabelę i porządkuje stan po restarcie procesu.

    Każdy wiersz zostawiony w stanie 'running' oznacza, że proces padł w trakcie
    (redeploy Railway) — przepisujemy go na 'failed', inaczej aplikacja
    odpytywałaby w nieskończoność o zadanie, które już nie istnieje.
    """
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text(_CREATE_TABLE_SQL))
            await session.execute(text(_CREATE_INDEX_SQL))
            await session.execute(
                text(
                    "UPDATE job_runs SET state = 'failed', "
                    "error = 'przerwane: restart serwera', finished_at = now() "
                    "WHERE state = 'running'"
                )
            )
            await session.commit()

            rows = (
                await session.execute(
                    text(
                        "SELECT DISTINCT ON (job) job, run_id, state, trigger, stage, "
                        "stage_detail, stage_index, stage_count, progress_current, "
                        "progress_total, progress_unit, counters, started_at, finished_at, "
                        "result, error FROM job_runs ORDER BY job, started_at DESC NULLS LAST"
                    )
                )
            ).mappings().all()

        for row in rows:
            record = _JOBS.get(row["job"])
            if record is None:
                continue
            record.state = row["state"]
            record.run_id = row["run_id"]
            record.trigger = row["trigger"]
            record.stage = row["stage"]
            record.stage_detail = row["stage_detail"]
            record.stage_index = row["stage_index"]
            record.stage_count = row["stage_count"] or record.stage_count
            record.current = row["progress_current"]
            record.total = row["progress_total"]
            record.unit = row["progress_unit"]
            record.counters = dict(row["counters"] or {})
            record.started_at = row["started_at"]
            record.finished_at = row["finished_at"]
            record.result = row["result"]
            record.error = row["error"]
            if row["state"] == "succeeded":
                record.last_success_at = row["finished_at"]

        log_info(f"🗂️ Rejestr zadań gotowy ({len(_JOBS)} zadań, {len(rows)} wpisów historii).")
    except Exception as exc:  # noqa: BLE001 — brak trwałości nie może zablokować startu API
        log_error(f"⚠️ Nie udało się zainicjalizować tabeli job_runs: {exc}")
