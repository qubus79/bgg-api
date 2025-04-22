from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import BGGGame
from app.scraper_bgg import fetch_bgg_collection
from app.utils import log_info, log_success
from app.database import engine
from app.models import Base

USERNAME = "qubus"

async def init_bgg_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def update_bgg_collection() -> dict:
    log_info("Inicjalizacja bazy BGG...")
    await init_bgg_db()

    log_info("Rozpoczynam pobieranie danych z BGG kolekcji...")
    inserted, updated = 0, 0

    async with AsyncSessionLocal() as session:
        # fetch_bgg_collection teraz sam iteruje po game_id i zapisuje dane
        result = await session.execute(select(BGGGame.game_id))
        existing_ids = {row[0] for row in result.all()}

        changes = await fetch_bgg_collection(USERNAME, session, existing_ids)
        inserted += changes["inserted"]
        updated += changes["updated"]

        await session.commit()

    log_success(f"Zaktualizowano kolekcję: {inserted} nowych, {updated} zaktualizowanych")
    return {"inserted": inserted, "updated": updated, "total": inserted + updated}


async def get_bgg_collection() -> list:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGGame))
        return [row.__dict__ for row in result.scalars().all()]
