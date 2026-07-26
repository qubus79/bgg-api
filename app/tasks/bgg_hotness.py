from app.scraper.bgg_hotness import fetch_bgg_hotness_games, fetch_bgg_hotness_persons
from app import jobs
from app.database import AsyncSessionLocal
from app.models.bgg_hotness import BGGHotGame, BGGHotPerson
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, text
from app.utils.logging import log_info, log_success, log_warning, log_error
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger


# ---------------- HOT GAMES ----------------

async def update_hot_games(ctx=jobs.NULL_CTX):
    log_info("🔄 Aktualizacja listy hot games z BGG")
    games_data = await fetch_bgg_hotness_games(ctx=ctx)
    ctx.set_stage("db_sync", total=len(games_data), unit="gier", index=3, count=3)
    ctx.set_progress(len(games_data))

    async with AsyncSessionLocal() as session:
        await clear_hot_games(session)  # 🚮 usuń stare wpisy
        session.add_all([BGGHotGame(**game) for game in games_data])
        await session.commit()

    log_success(f"✅ Hotness games zapisane: {len(games_data)} gier")
    return {"status": "done", "total": len(games_data)}


async def get_hot_games():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGHotGame))
        return result.scalars().all()


async def clear_hot_games(session: AsyncSession):
    await session.execute(delete(BGGHotGame))
    await session.execute(text("ALTER SEQUENCE bgg_hot_games_id_seq RESTART WITH 1"))


async def get_hotness_game_stats():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM bgg_hot_games"))
        count = result.scalar()

        result2 = await session.execute(text("SELECT MAX(last_modified) FROM bgg_hot_games"))
        last_update = result2.scalar()

        return {
            "count": count or 0,
            "last_update": str(last_update) if last_update else "n/a"
        }


# ---------------- HOT PERSONS ----------------

async def update_hot_persons(ctx=jobs.NULL_CTX):
    log_info("🔄 Aktualizacja listy hot persons z BGG")
    persons_data = await fetch_bgg_hotness_persons(ctx=ctx)
    ctx.set_stage("db_sync", total=len(persons_data), unit="osób", index=2, count=2)
    ctx.set_progress(len(persons_data))

    async with AsyncSessionLocal() as session:
        await clear_hot_persons(session)  # 🚮 usuń stare wpisy
        session.add_all([BGGHotPerson(**person) for person in persons_data])
        await session.commit()

    log_success(f"✅ Hotness persons zapisane: {len(persons_data)} osób")
    return {"status": "done", "total": len(persons_data)}


async def get_hot_persons():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGHotPerson))
        return result.scalars().all()


async def clear_hot_persons(session: AsyncSession):
    await session.execute(delete(BGGHotPerson))
    await session.execute(text("ALTER SEQUENCE bgg_hot_persons_id_seq RESTART WITH 1"))


async def get_hotness_person_stats():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM bgg_hot_persons"))
        count = result.scalar()

        result2 = await session.execute(text("SELECT MAX(last_modified) FROM bgg_hot_persons"))
        last_update = result2.scalar()

        return {
            "count": count or 0,
            "last_update": str(last_update) if last_update else "n/a"
        }


# ---------------- SCHEDULER ----------------

async def setup_hotness_scheduler():
    log_info("🕒 Scheduler started: Hotness aktualizuje się co 4 godziny.")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_scheduled_hot_games, IntervalTrigger(hours=4), id="update_hot_games", replace_existing=True)
    scheduler.add_job(_scheduled_hot_persons, IntervalTrigger(hours=4), id="update_hot_persons", replace_existing=True)
    scheduler.start()
    log_success("✅ Hotness scheduler uruchomiony")


async def _scheduled_hot_games():
    """Zaplanowany bieg przez rejestr — widoczny w aplikacji, wspólna blokada."""
    await jobs.start("bgg_hotness_games", trigger="schedule")


async def _scheduled_hot_persons():
    await jobs.start("bgg_hotness_persons", trigger="schedule")
