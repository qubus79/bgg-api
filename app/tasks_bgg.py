# app/tasks_bgg.py
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import BGGCollectionItem
from app.scraper_bgg import fetch_bgg_collection
from app.utils import log_info, log_success

USERNAME = "qubus"

async def update_bgg_collection() -> dict:
    log_info(f"Pobieranie kolekcji BGG dla użytkownika: {USERNAME}...")
    games = await fetch_bgg_collection(USERNAME)
    log_info(f"Pobrano {len(games)} gier z kolekcji")

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(BGGCollectionItem))
        existing_map = {g.bgg_id: g for g in existing.scalars().all()}

        inserted, updated = 0, 0

        for game in games:
            bgg_id = game.get("game_id")
            if not bgg_id:
                continue

            if bgg_id in existing_map:
                db_game = existing_map[bgg_id]
                db_game.title = game.get("name")
                db_game.year_published = game.get("year_published")
                db_game.image = game.get("image")
                db_game.thumbnail = game.get("thumbnail")
                db_game.status_owned = game.get("own")
                db_game.status_prevowned = game.get("prev_owned")
                db_game.status_wishlist = game.get("wishlist")
                db_game.status_preordered = game.get("preordered")
                db_game.status_wanttoplay = game.get("want_to_play")
                db_game.status_wanttobuy = game.get("want_to_buy")
                updated += 1
            else:
                new_game = BGGCollectionItem(
                    bgg_id=game.get("game_id"),
                    title=game.get("name"),
                    year_published=game.get("year_published"),
                    image=game.get("image"),
                    thumbnail=game.get("thumbnail"),
                    status_owned=game.get("own"),
                    status_prevowned=game.get("prev_owned"),
                    status_wishlist=game.get("wishlist"),
                    status_preordered=game.get("preordered"),
                    status_wanttoplay=game.get("want_to_play"),
                    status_wanttobuy=game.get("want_to_buy"),
                )
                session.add(new_game)
                inserted += 1

        await session.commit()

    log_success(f"Zaktualizowano kolekcję: {inserted} nowych, {updated} zaktualizowanych")
    return {"inserted": inserted, "updated": updated, "total": len(games)}

async def get_bgg_collection():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGCollectionItem))
        return result.scalars().all()
