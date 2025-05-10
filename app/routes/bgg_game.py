# app/routes/bgg_game.py

from fastapi import APIRouter
from app.tasks import bgg_game

router = APIRouter(prefix="/bgg_games", tags=["BGG Games"])

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/stats")
async def stats():
    return await bgg_game.get_stats()

@router.post("/update_bgg_collection")
async def update_bgg():
    return await bgg_game.update_bgg_collection()

@router.get("/bgg_collection")
async def get_bgg():
    return await bgg_game.get_bgg_collection()
