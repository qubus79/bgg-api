# app/tasks.py
from sqlalchemy import text, select
from app.database import AsyncSessionLocal, engine, Base
from app.models import Premiere
from app.utils import log_info, log_success
from app.scraper import fetch_all_premieres_raw
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# do testów
from app.utils import log_info, log_success, log_warning, log_error


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def setup_scheduler():
    log_info("Scheduler started. Updating premieres every 2 hours.")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(update_premieres, IntervalTrigger(seconds=180), id="update_premieres_job", replace_existing=True)
    scheduler.start()


async def get_stats():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM premieres"))
        count = result.scalar()

        result2 = await session.execute(text("SELECT MAX(created_at) FROM premieres"))
        last_update = result2.scalar()

        return {
            "count": count or 0,
            "last_update": str(last_update) if last_update else "n/a"
        }


async def get_all_premieres():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT * FROM premieres"))
        rows = result.mappings().all()
        return [dict(row) for row in rows]


async def update_premieres():
    log_info("Start update_premieres...")

    games = await fetch_all_premieres_raw()
    log_info(f"Pobrano {len(games)} premier z listy")

    async with AsyncSessionLocal() as session:
        existing_entries = await session.execute(select(Premiere))
        existing_by_game_id = {p.game_id: p for p in existing_entries.scalars().all() if p.game_id}

        # tasks.py (tymczasowo, przed pętlą for)
        from collections import Counter

        ## TEST
        id_counts = Counter([g["game_id"] for g in games if g["game_id"]])
        dupes = [gid for gid, count in id_counts.items() if count > 1]
        if dupes:
            for d in dupes:
                log_warning(f"DUPE game_id in input: {d}")
            raise ValueError("Znaleziono duplikaty game_id w danych wejściowych")
        ## END TEST


        for idx, game in enumerate(games, 1):
            log_info(f"[{idx}/{len(games)}] {game['game_name']} - Aktualizacja")

            if game["game_id"] in existing_by_game_id:
                existing = existing_by_game_id[game["game_id"]]
                existing.game_name = game["game_name"]
                existing.designers = game["designers"]
                existing.status = game["status"]
                existing.release_date = game["release_date"]
                existing.release_period = game["release_period"]
                existing.release_year = game["release_year"]
                existing.publisher = game["publisher"]
                existing.game_type = game["game_type"]
                existing.additional_info = game["additional_info"]
                existing.game_image = game["game_image"]
                existing.game_url = game["game_url"]
                existing.additional_details = game["additional_details"]
                # interest_level pozostaje bez zmian
            else:
                p = Premiere(
                    game_id=game["game_id"],
                    game_name=game["game_name"],
                    designers=game["designers"],
                    status=game["status"],
                    release_date=game["release_date"],
                    release_period=game["release_period"],
                    release_year=game["release_year"],
                    publisher=game["publisher"],
                    game_type=game["game_type"],
                    additional_info=game["additional_info"],
                    game_image=game["game_image"],
                    game_url=game["game_url"],
                    additional_details=game["additional_details"],
                    interest_level=None
                )
                session.add(p)

        await session.commit()

    log_success(f"Update completed: {len(games)} entries checked/updated")
    return {"status": "ok", "checked": len(games)}


async def update_interest_level(game_id: int, level: str | None) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Premiere).where(Premiere.game_id == game_id))
        premiere = result.scalar_one_or_none()
        if premiere:
            premiere.interest_level = level
            await session.commit()
            return True
        return False
