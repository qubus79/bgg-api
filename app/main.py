# app/main.py

from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import engine, Base, get_db
from app.routes import bgg_game
from app.routes import bgg_accessory
from app.models.bgg_game import BGGGame
from app.models.bgg_accessory import BGGAccessory
from app.tasks.bgg_game import setup_scheduler
from app.tasks.bgg_accessory import setup_accessory_scheduler
from app.utils.logging import log_info

app = FastAPI()

# Tworzenie tabel w bazie danych
async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Inicjalizacja aplikacji i schedulerów
@app.on_event("startup")
async def startup_event():
    await create_tables()
    await setup_game_scheduler()
    await setup_accessory_scheduler()
    log_info("✅ Application started and both schedulers initialized.")

# Rejestracja routerów
app.include_router(bgg_game.router)
app.include_router(bgg_accessory.router)

# Główny endpoint z podsumowaniem
@app.get("/")
async def read_root(db: AsyncSession = Depends(get_db)):
    games = (await db.execute(select(BGGGame))).scalars().all()
    accessories = (await db.execute(select(BGGAccessory))).scalars().all()

    return {
        "message": "BGG API is running!",
        "status": "ok",
        "bgg_games_count": len(games),
        "bgg_accessories_count": len(accessories),
    }

# Własny handler 404
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={"error": "Resource not found", "status": "fail"}
    )
