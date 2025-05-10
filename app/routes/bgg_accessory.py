# app/routes/bgg_accessory.py

from fastapi import APIRouter
from app.tasks import bgg_accessory

router = APIRouter(prefix="/bgg_accessories", tags=["BGG Accessories"])

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/stats")
async def stats():
    return await bgg_accessory.get_stats()

@router.post("/update")
async def update_bgg_accessories():
    return await bgg_accessory.update_bgg_accessories()

@router.get("/")
async def get_bgg_accessories():
    return await bgg_accessory.get_bgg_accessories()
