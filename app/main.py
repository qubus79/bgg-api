# app/main.py
from fastapi import FastAPI
from app import tasks_bgg

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    await tasks_bgg.init_bgg_db()

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/update_bgg_collection")
async def update_bgg():
    return await tasks_bgg.update_bgg_collection()

@app.get("/bgg_collection")
async def get_bgg():
    return await tasks_bgg.get_bgg_collection()
