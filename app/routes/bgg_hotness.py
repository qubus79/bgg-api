# app/routes/bgg_hotness.py

from fastapi import APIRouter
from app.tasks import bgg_hotness

router = APIRouter(prefix="/bgg_hotness", tags=["BGG Hotness"])

# ----------------------------- GAMES -----------------------------

@router.get("/games/health")
async def games_health():
    return {"status": "ok"}

@router.get("/games/stats")
async def games_stats():
    return await bgg_hotness.get_hotness_game_stats()

@router.post("/games/update")
async def update_hotness_games():
    return await bgg_hotness.update_bgg_hotness_games()

@router.get("/games")
async def get_hotness_games():
    return await bgg_hotness.get_bgg_hotness_games()


# ----------------------------- PERSONS -----------------------------

@router.get("/persons/health")
async def persons_health():
    return {"status": "ok"}

@router.get("/persons/stats")
async def persons_stats():
    return await bgg_hotness.get_hotness_person_stats()

@router.post("/persons/update")
async def update_hotness_persons():
    return await bgg_hotness.update_bgg_hotness_persons()

@router.get("/persons")
async def get_hotness_persons():
    return await bgg_hotness.get_bgg_hotness_persons()
