# app/tasks_bgg.py

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import BGGGame
from app.scraper_bgg import fetch_bgg_collection
from app.utils import log_info, log_success


async def update_bgg_collection(username: str) -> dict:
    log_info(f"Pobieranie kolekcji BGG dla użytkownika: {username}...")
    games = await fetch_bgg_collection(username)
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
                db_game.name = game.get("name")
                db_game.year_published = game.get("year_published")
                db_game.image = game.get("image")
                db_game.thumbnail = game.get("thumbnail")
                db_game.status = game.get("status")
                updated += 1
            else:
                new_game = BGGGame(
                    game_id=game.get("game_id"),
                    name=game.get("name"),
                    year_published=game.get("year_published"),
                    image=game.get("image"),
                    thumbnail=game.get("thumbnail"),
                    status=game.get("status"),
                )
                session.add(new_game)
                inserted += 1

        await session.commit()

    log_success(f"Zaktualizowano kolekcję: {inserted} nowych, {updated} zaktualizowanych")
    return {"inserted": inserted, "updated": updated, "total": len(games)}
