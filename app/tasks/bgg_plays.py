import os
from sqlalchemy import select, text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app import jobs
from app.database import AsyncSessionLocal, engine, Base
from app.models.bgg_plays import BGGPlay
from app.scraper.bgg_plays import update_bgg_plays_from_collection
from app.scraper.bgg_plays_user import update_bgg_plays_for_user
from app.utils.logging import log_info, log_success


# Jak często synchronizować plays (domyślnie co 6h, bo to cięższe niż kolekcja)
PLAYS_SYNC_HOURS = int(os.getenv("BGG_PLAYS_SYNC_HOURS", "6"))

# Skąd brać rozgrywki:
#   "user" — jeden dziennik `xmlapi2/plays?username=…`, komplet w kilkunastu
#            zapytaniach; każdy przebieg pełny, więc widać też edycje
#            komentarzy przy starych partiach,
#   "game" — stara ścieżka, zapytanie na każdą grę z kolekcji.
# Przełącznik zostaje, bo nowe źródło nie niesie trzech pól starego; gdyby
# okazało się to na żywo dotkliwsze, niż zakładam, powrót to jedna zmienna.
PLAYS_SOURCE = os.getenv("BGG_PLAYS_SOURCE", "user").strip().lower()
PLAYS_USERNAME = os.getenv("BGG_USERNAME", "qubus")


async def init_plays_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def setup_plays_scheduler():
    log_info(f"Scheduler started. Updating BGG plays every {PLAYS_SYNC_HOURS} hours.")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _scheduled_plays,
        IntervalTrigger(hours=PLAYS_SYNC_HOURS),
        id="update_bgg_plays_job",
        replace_existing=True,
    )
    scheduler.start()


# Rozbicie rozgrywek po statusie gry w kolekcji. Liczone w WIERSZACH, tak samo
# jak `count` — inaczej wychodziłoby zestawienie dwóch różnych jednostek, bo
# `bgg_collection.num_plays` sumuje `quantity` (partia „zagrane 3×" to jeden
# wiersz, ale trzy w num_plays).
#
# `LEFT JOIN` jest bezpieczny: `bgg_collection.bgg_id` ma UNIQUE, więc żadna
# rozgrywka nie policzy się dwa razy.
_BREAKDOWN_SQL = """
SELECT count(*)                                                        AS total,
       count(*) FILTER (WHERE coalesce(c.status_owned, false))         AS owned,
       count(*) FILTER (WHERE c.bgg_id IS NULL)                        AS outside_collection,
       count(*) FILTER (WHERE c.bgg_id IS NOT NULL
                          AND NOT coalesce(c.status_owned, false))     AS in_collection_not_owned
FROM bgg_plays p
LEFT JOIN bgg_collection c ON c.bgg_id = p.object_id
"""


async def get_plays_stats():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM bgg_plays"))
        count = result.scalar()

        result2 = await session.execute(text("SELECT MAX(updated_at) FROM bgg_plays"))
        last_update = result2.scalar()

        stats = {
            "count": int(count or 0),
            "last_update": str(last_update) if last_update else "n/a",
        }

        try:
            row = (await session.execute(text(_BREAKDOWN_SQL))).mappings().first()
        except Exception as exc:  # noqa: BLE001 — rozbicie to dodatek, nie może wywrócić statusu
            log_info(f"⚠️ Nie udało się policzyć rozbicia rozgrywek: {exc}")
            return stats

        if row:
            stats["owned"] = int(row["owned"] or 0)
            stats["outside_collection"] = int(row["outside_collection"] or 0)
            stats["in_collection_not_owned"] = int(row["in_collection_not_owned"] or 0)
        return stats


async def update_bgg_plays(ctx=jobs.NULL_CTX) -> dict:
    """Synchronizuje rozgrywki. Źródło wybiera `BGG_PLAYS_SOURCE`."""
    log_info("Inicjalizacja bazy BGG Plays...")
    await init_plays_db()

    if PLAYS_SOURCE == "game":
        log_info("Rozpoczynam pobieranie plays z BGG (na podstawie gier w kolekcji DB)...")
        result = await update_bgg_plays_from_collection(ctx=ctx)
    else:
        log_info(f"Rozpoczynam pobieranie dziennika rozgrywek BGG ({PLAYS_USERNAME})...")
        result = await update_bgg_plays_for_user(PLAYS_USERNAME, ctx=ctx)

    log_success("🎉 Plays z BGG zostały zsynchronizowane z bazą danych")
    return {"status": "done", **(result or {})}


def _model_to_dict(obj) -> dict:
    d = dict(obj.__dict__)
    d.pop("_sa_instance_state", None)
    return d


async def get_bgg_plays(limit: int = 2000, offset: int = 0, bgg_id: int | None = None) -> list:
    """
    Read plays from DB.
    Optional filter: bgg_id == object_id.
    """
    async with AsyncSessionLocal() as session:
        stmt = select(BGGPlay).order_by(
            BGGPlay.play_date.desc().nullslast(),
            BGGPlay.tstamp.desc().nullslast(),
        )

        if bgg_id is not None:
            stmt = stmt.where(BGGPlay.object_id == int(bgg_id))

        stmt = stmt.limit(limit).offset(offset)

        result = await session.execute(stmt)
        return [_model_to_dict(row) for row in result.scalars().all()]
    
async def get_plays_stats_per_game():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGPlay))
        plays = result.scalars().all()

    stats = {}

    for p in plays:
        gid = p.object_id
        if gid not in stats:
            stats[gid] = {
                "bgg_id": gid,
                "game_name": p.game_name,
                "plays": 0,
                "total_quantity": 0,
                "wins": 0,
            }

        stats[gid]["plays"] += 1
        stats[gid]["total_quantity"] += p.quantity or 1

        if p.players:
            for pl in p.players:
                if pl.get("username") and pl.get("win") in ("1", 1, True):
                    stats[gid]["wins"] += 1

    return list(stats.values())


async def get_plays_stats_per_player():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGPlay))
        plays = result.scalars().all()

    stats = {}

    for p in plays:
        if not p.players:
            continue

        for pl in p.players:
            key = pl.get("username") or pl.get("name")
            if not key:
                continue

            if key not in stats:
                stats[key] = {
                    "player": key,
                    "plays": 0,
                    "wins": 0,
                }

            stats[key]["plays"] += 1
            if pl.get("win") in ("1", 1, True):
                stats[key]["wins"] += 1

    return list(stats.values())


async def get_my_plays_stats(username: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGPlay))
        plays = result.scalars().all()

    total = 0
    wins = 0

    for p in plays:
        if not p.players:
            continue

        for pl in p.players:
            if pl.get("username") == username:
                total += 1
                if pl.get("win") in ("1", 1, True):
                    wins += 1

    return {
        "username": username,
        "plays": total,
        "wins": wins,
    }

async def _scheduled_plays():
    """Zaplanowany bieg przez rejestr — widoczny w aplikacji, wspólna blokada."""
    await jobs.start("bgg_plays", trigger="schedule")
