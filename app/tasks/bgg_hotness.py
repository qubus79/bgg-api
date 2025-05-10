# tasks/bgg_hotness.py

from app.scraper.bgg_hotness import fetch_bgg_hotness_games, fetch_bgg_hotness_persons
from app.database import AsyncSessionLocal
from app.models.bgg_hotness import BGGHotGame
from app.models.bgg_hotness import BGGHotPerson
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.utils.logging import log_info, log_success

# ---------------- HOT GAMES ----------------

async def update_hot_games():
    log_info("🔄 Aktualizacja listy hot games z BGG")
    games_data = await fetch_bgg_hotness_games()
    async with AsyncSessionLocal() as session:
        await clear_hot_games(session)
        session.add_all([BGGHotGame(**game) for game in games_data])
        await session.commit()
    log_success(f"✅ Zapisano {len(games_data)} gier z Hotness")
    return {"status": "done", "count": len(games_data)}

async def get_hot_games():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGHotGame))
        return result.scalars().all()

async def clear_hot_games(session: AsyncSession):
    await session.execute(delete(BGGHotGame))


# ---------------- HOT PERSONS ----------------

async def update_hot_persons():
    log_info("🔄 Aktualizacja listy hot persons z BGG")
    persons_data = await fetch_bgg_hot_persons()
    async with AsyncSessionLocal() as session:
        await clear_hot_persons(session)
        session.add_all([BGGHotPerson(**person) for person in persons_data])
        await session.commit()
    log_success(f"✅ Zapisano {len(persons_data)} osób z Hotness")
    return {"status": "done", "count": len(persons_data)}

async def get_hot_persons():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGHotPerson))
        return result.scalars().all()

async def clear_hot_persons(session: AsyncSession):
    await session.execute(delete(BGGHotPerson))
