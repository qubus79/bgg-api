from sqlalchemy import select, text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.database import AsyncSessionLocal, engine
from app.models.bgg_accessory import BGGAccessory, Base
from app.scraper.bgg_accessory import fetch_bgg_accessories
from app.utils.logging import log_info, log_success

USERNAME = "qubus"


async def init_bgg_accessory_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def setup_accessory_scheduler():
    log_info("Scheduler started. Updating BGG accessories every 6 hours.")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(update_bgg_accessories, IntervalTrigger(hours=6), id="update_bgg_accessory_job", replace_existing=True)
    scheduler.start()


async def get_accessory_stats():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM bgg_accessories"))
        count = result.scalar()

        result2 = await session.execute(text("SELECT MAX(last_modified) FROM bgg_accessories"))
        last_update = result2.scalar()

        return {
            "count": count or 0,
            "last_update": str(last_update) if last_update else "n/a"
        }


async def update_bgg_accessories() -> dict:
    log_info("Inicjalizacja bazy akcesoriów BGG...")
    await init_bgg_accessory_db()

    log_info("Rozpoczynam pobieranie danych z BGG akcesoriów...")
    await fetch_bgg_accessories(USERNAME)

    log_success("🎉 Akcesoria BGG zostały zsynchronizowane z bazą danych")
    return {"status": "done"}


async def get_bgg_accessories() -> list:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGAccessory))
        return [row.__dict__ for row in result.scalars().all()]
