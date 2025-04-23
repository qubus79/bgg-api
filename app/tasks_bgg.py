from sqlalchemy import select, text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.database import AsyncSessionLocal
from app.models import BGGGame
from app.scraper_bgg import fetch_bgg_collection
from app.utils import log_info, log_success
from app.database import engine
from app.models import Base
import asyncio

USERNAME = "qubus"


# do testów
from app.utils import log_info, log_success, log_warning, log_error


async def init_bgg_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def setup_scheduler():
    log_info("Scheduler started. Updating BGG collection every 6 hours.")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(update_bgg_collection, IntervalTrigger(hours=6), id="update_bgg_collection_job", replace_existing=True)
    scheduler.start()


async def get_stats():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM bgg_collection"))
        count = result.scalar()

        result2 = await session.execute(text("SELECT MAX(created_at) FROM bgg_collection"))
        last_update = result2.scalar()

        return {
            "count": count or 0,
            "last_update": str(last_update) if last_update else "n/a"
        }


async def update_bgg_collection() -> dict:
    log_info("Inicjalizacja bazy BGG...")
    await init_bgg_db()

    log_info("Rozpoczynam pobieranie danych z BGG kolekcji...")
    await fetch_bgg_collection(USERNAME)

    log_success("🎉 Kolekcja BGG została zsynchronizowana z bazą danych")
    return {"status": "done"}


async def get_bgg_collection() -> list:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGGame))
        return [row.__dict__ for row in result.scalars().all()]
