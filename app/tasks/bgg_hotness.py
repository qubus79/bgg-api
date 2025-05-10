from app.scraper.bgg_hotness import fetch_bgg_hotness_games, fetch_bgg_hotness_persons
from app.database import AsyncSessionLocal
from app.models.bgg_hotness import BGGHotGame, BGGHotPerson
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, text
from app.utils.logging import log_info, log_success, log_warning, log_error
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger


# ---------------- HOT GAMES ----------------

async def update_hot_games():
    log_info("🔄 Aktualizacja listy hot games z BGG")
    try:
        games_data = await fetch_bgg_hotness_games()
        log_info(f"📦 Otrzymano {len(games_data)} gier z BGG")

        async with AsyncSessionLocal() as session:
            await clear_hot_games(session)
            log_info("🗑 Usunięto stare dane hot games")

            session.add_all([BGGHotGame(**game) for game in games_data])
            await session.commit()
            log_success(f"✅ Zapisano {len(games_data)} gier z Hotness")

        return {"status": "done", "count": len(games_data)}

    except Exception as e:
        log_error(f"❌ Błąd podczas aktualizacji hot games: {e}")
        return {"status": "error", "message": str(e)}


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

async def update_hot_persons():
    log_info("🔄 Aktualizacja listy hot persons z BGG")
    try:
        persons_data = await fetch_bgg_hotness_persons()
        log_info(f"📦 Otrzymano {len(persons_data)} osób z BGG")

        async with AsyncSessionLocal() as session:
            await clear_hot_persons(session)
            log_info("🗑 Usunięto stare dane hot persons")

            session.add_all([BGGHotPerson(**person) for person in persons_data])
            await session.commit()
            log_success(f"✅ Zapisano {len(persons_data)} osób z Hotness")

        return {"status": "done", "count": len(persons_data)}

    except Exception as e:
        log_error(f"❌ Błąd podczas aktualizacji hot persons: {e}")
        return {"status": "error", "message": str(e)}


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
    log_info("🕒 Scheduler started: Hotness aktualizuje się co 2 godziny.")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(update_hot_games, IntervalTrigger(hours=2), id="update_hot_games", replace_existing=True)
    scheduler.add_job(update_hot_persons, IntervalTrigger(hours=2), id="update_hot_persons", replace_existing=True)
    scheduler.start()
    log_success("✅ Hotness scheduler uruchomiony")
