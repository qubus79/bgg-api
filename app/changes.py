# app/changes.py

"""Same zmiany dla aplikacji (`/…/changes?since=`) zamiast całych list.

Zasady (te same co w games-api i sleeves-api):

- `cursor` — najwcześniejszy początek transakcji, która jeszcze trwa w tej
  bazie (albo „teraz"); aplikacja odsyła go jako `since`, a serwer i tak cofa
  go o `CHANGES_OVERLAP`. Wiersz widać dopiero po zatwierdzeniu transakcji,
  a datę zmiany dostaje wcześniej — gdyby `cursor` był po prostu „teraz",
  zmiana z trwającej transakcji przeszłaby przez sito,
- daty zmian i usunięć to czas bazy (`now()` — początek transakcji), nie
  zegar serwera aplikacji,
- zmiana = `changed_at` wiersza (ustawiane tylko przy prawdziwej zmianie
  danych); wiersz bez daty zmiany wraca zawsze,
- usunięte — ślady w `removed_rows` (trzymane `REMOVED_KEEP`),
- `total` — ile wierszy jest razem; aplikacja porównuje ze swoją liczbą
  i przy różnicy pobiera całość. Całość i tak raz na dobę.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.models.removed_row import RemovedRow

CHANGES_OVERLAP = timedelta(minutes=5)
REMOVED_KEEP = timedelta(days=60)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Data zmiany wiersza — czas bazy (początek transakcji), zgodny z `cursor`.
db_now = func.now

# Najwcześniejszy początek trwającej transakcji (innej niż ta) albo „teraz".
_CURSOR_SQL = text(
    "SELECT LEAST(now(), COALESCE(("
    "  SELECT min(xact_start) FROM pg_stat_activity"
    "  WHERE datname = current_database() AND backend_type = 'client backend'"
    "    AND xact_start IS NOT NULL AND pid <> pg_backend_pid()"
    "), now()))"
)


def cursor_text(moment: datetime) -> str:
    # „Z" zamiast „+00:00" — plus w adresie zapytania zamienia się w spację.
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def record_removed(session, kind: str, keys) -> None:
    for key in keys:
        statement = pg_insert(RemovedRow).values(kind=kind, key=int(key), removed_at=func.now())
        await session.execute(statement.on_conflict_do_update(
            index_elements=[RemovedRow.kind, RemovedRow.key],
            set_={"removed_at": statement.excluded.removed_at},
        ))


async def forget_removed(session, kind: str, keys) -> None:
    """Wrócił do kolekcji — już nie jest „usunięty"."""
    keys = [int(key) for key in keys]
    if keys:
        await session.execute(delete(RemovedRow).where(RemovedRow.kind == kind, RemovedRow.key.in_(keys)))


async def changes(model, kind: str, since: datetime, serialize: Callable[[Any], dict], items_key: str) -> dict:
    async with AsyncSessionLocal() as session:
        cursor = (await session.execute(_CURSOR_SQL)).scalar()
        after = since - CHANGES_OVERLAP
        rows = (await session.execute(
            select(model).where(or_(model.changed_at.is_(None), model.changed_at >= after))
        )).scalars().all()
        removed = (await session.execute(
            select(RemovedRow.key).where(RemovedRow.kind == kind, RemovedRow.removed_at >= after)
        )).scalars().all()
        total = (await session.execute(select(func.count()).select_from(model))).scalar() or 0
        # Przed `commit` — po nim SQLAlchemy czyści załadowane pola wierszy.
        items = [serialize(row) for row in rows]
        await session.execute(delete(RemovedRow).where(RemovedRow.removed_at < cursor - REMOVED_KEEP))
        await session.commit()
    return {
        "cursor": cursor_text(cursor),
        items_key: items,
        "removed": sorted(removed),
        "total": total,
    }


def row_dict(row) -> dict:
    """Wiersz jak w pełnych listach (`row.__dict__`), bez stanu SQLAlchemy."""
    return {key: value for key, value in row.__dict__.items() if not key.startswith("_")}
