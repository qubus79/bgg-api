# app/main.py
from fastapi import FastAPI, HTTPException, Path
from app import tasks
from app.schemas import InterestLevelUpdate

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    await tasks.init_db()
    await tasks.setup_scheduler()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/premieres")
async def get_all_premieres():
    return await tasks.get_all_premieres()


@app.get("/stats")
async def stats():
    return await tasks.get_stats()


@app.post("/update_premieres")
async def update():
    return await tasks.update_premieres()


@app.patch("/premieres/{game_id}/interest")
async def update_interest_level(game_id: int = Path(..., description="Stable game ID from the website"), payload: InterestLevelUpdate = ...):
    success = await tasks.update_interest_level(game_id, payload.interest_level)
    if not success:
        raise HTTPException(status_code=404, detail="Game not found")
    return {"status": "ok", "game_id": game_id, "interest_level": payload.interest_level}

# --- Nowe endpointy dla kolekcji BGG ---

@app.post("/update_bgg_collection")
async def update_bgg():
    return await tasks.update_bgg_collection()


@app.get("/bgg_collection")
async def get_bgg():
    return await tasks.get_bgg_collection()
