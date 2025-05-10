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
    games_data = await fetch_bgg_hotness_games()
    updated, added = 0, 0

    async with AsyncSessionLocal() as session:
        for game_data in games_data:
            result = await session.execute(select(BGGHotGame).where(BGGHotGame.bgg_id == game_data["bgg_id"]))
            existing = result.scalars().first()

            if existing:
                for field, value in game_data.items():
                    setattr(existing, field, value)
                updated += 1
            else:
                session.add(BGGHotGame(**game_data))
                added += 1

        await session.commit()

    log_success(f"✅ Hotness games zapisane: {added} nowych, {updated} zaktualizowanych")
    return {"status": "done", "added": added, "updated": updated}


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
    persons_data = await fetch_bgg_hotness_persons()
    updated, added = 0, 0

    async with AsyncSessionLocal() as session:
        for person_data in persons_data:
            result = await session.execute(select(BGGHotPerson).where(BGGHotPerson.bgg_id == person_data["bgg_id"]))
            existing = result.scalars().first()

            if existing:
                for field, value in person_data.items():
                    setattr(existing, field, value)
                updated += 1
            else:
                session.add(BGGHotPerson(**person_data))
                added += 1

        await session.commit()

    log_success(f"✅ Hotness persons zapisane: {added} nowych, {updated} zaktualizowanych")
    return {"status": "done", "added": added, "updated": updated}


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
    scheduler.add_job(update_hot_games, IntervalTrigger(hours=4), id="update_hot_games", replace_existing=True)
    scheduler.add_job(update_hot_persons, IntervalTrigger(hours=4), id="update_hot_persons", replace_existing=True)
    scheduler.start()
    log_success("✅ Hotness scheduler uruchomiony")
