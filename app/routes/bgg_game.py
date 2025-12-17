# app/routes/bgg_game.py

from fastapi import APIRouter, Query
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

@router.get("/purchases")
async def get_purchases(
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """Lightweight list of purchases with status and type."""
    return await bgg_game.get_bgg_purchases(limit=limit, offset=offset)

@router.get("/purchases/all")
async def get_purchases_all():
    """Return all purchases without pagination.

    Intended for internal use, exports, and batch processing.
    """
    # Use a very high limit to effectively disable pagination
    return await bgg_game.get_bgg_purchases(limit=10000, offset=0)

@router.get("/purchases/stats")
async def get_purchase_stats():
    """Aggregated purchase statistics (currency, status, type)."""
    return await bgg_game.get_bgg_purchase_stats()
