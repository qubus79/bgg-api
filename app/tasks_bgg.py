# app/tasks_bgg.py

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import BGGGame
from app.scraper_bgg import fetch_bgg_collection_with_details
from app.utils import log_info, log_success


async def update_bgg_collection() -> dict:
    log_info("Pobieranie rozszerzonej kolekcji BGG...")
    games = await fetch_bgg_collection(qubus)
    log_info(f"Pobrano {len(games)} gier z kolekcji")

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(BGGGame))
        existing_map = {g.game_id: g for g in existing.scalars().all()}

        inserted, updated = 0, 0

        for game in games:
            game_id = game.get("game_id")
            if not game_id:
                continue

            if game_id in existing_map:
                db_game = existing_map[game_id]
                for field, value in game.items():
                    setattr(db_game, field, value)
                updated += 1
            else:
                new_game = BGGGame(**game)
                session.add(new_game)
                inserted += 1

        await session.commit()

    log_success(f"Zaktualizowano kolekcję: {inserted} nowych, {updated} zaktualizowanych")
    return {"inserted": inserted, "updated": updated, "total": len(games)}


async def get_bgg_collection() -> list:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGGame))
        return [row.__dict__ for row in result.scalars().all()]
