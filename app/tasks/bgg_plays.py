import os
import asyncio
from sqlalchemy import select, text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.database import AsyncSessionLocal, engine, Base
from app.models.bgg_plays import BGGPlay
from app.scraper.bgg_plays import update_bgg_plays_from_collection
from app.utils.logging import log_info, log_success, log_warning


# Jak często synchronizować plays (domyślnie co 6h, bo to cięższe niż kolekcja)
PLAYS_SYNC_HOURS = int(os.getenv("BGG_PLAYS_SYNC_HOURS", "6"))


async def init_plays_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def setup_plays_scheduler():
    log_info(f"Scheduler started. Updating BGG plays every {PLAYS_SYNC_HOURS} hours.")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        update_bgg_plays,
        IntervalTrigger(hours=PLAYS_SYNC_HOURS),
        id="update_bgg_plays_job",
        replace_existing=True,
    )
    scheduler.start()


async def get_plays_stats():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM bgg_plays"))
        count = result.scalar()

        result2 = await session.execute(text("SELECT MAX(updated_at) FROM bgg_plays"))
        last_update = result2.scalar()

        return {
            "count": int(count or 0),
            "last_update": str(last_update) if last_update else "n/a",
        }


async def update_bgg_plays() -> dict:
    """
    Sync plays for games that exist in our collection DB (cross-reference by bgg_id).
    Uses authenticated cookies via existing auth mechanism inside the scraper.
    """
    log_info("Inicjalizacja bazy BGG Plays...")
    await init_plays_db()

    log_info("Rozpoczynam pobieranie plays z BGG (na podstawie gier w kolekcji DB)...")
    result = await update_bgg_plays_from_collection()

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