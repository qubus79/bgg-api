from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import BGGGame
from app.scraper_bgg import fetch_bgg_collection
from app.utils import log_info, log_success
from app.database import engine
from app.models import Base
import asyncio

USERNAME = "qubus"

async def init_bgg_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def update_bgg_collection() -> dict:
    log_info("Inicjalizacja bazy BGG...")
    await init_bgg_db()

    log_info("Rozpoczynam pobieranie danych z BGG kolekcji...")
    games = await fetch_bgg_collection(USERNAME)
    log_info(f"Pobrano {len(games)} gier z kolekcji")

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(BGGGame.bgg_id))
        existing_map = {g: True for g in existing.scalars().all()}

        inserted, updated = 0, 0

        for game in games:
            bgg_id = game.get("bgg_id")
            if not bgg_id:
                continue

            if bgg_id in existing_map:
                await session.execute(
                    select(BGGGame).filter(BGGGame.bgg_id == bgg_id).execution_options(synchronize_session="fetch")
                )
                await session.merge(BGGGame(**game))
                updated += 1
            else:
                session.add(BGGGame(**game))
                inserted += 1

            await session.commit()
            await asyncio.sleep(5.5)  # zachowanie limitu BGG

    log_success(f"Zaktualizowano kolekcję: {inserted} nowych, {updated} zaktualizowanych")
    return {"inserted": inserted, "updated": updated, "total": len(games)}


async def get_bgg_collection() -> list:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGGame))
        return [row.__dict__ for row in result.scalars().all()]
