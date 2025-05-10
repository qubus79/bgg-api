# app/schemas/bgg_hotness.py
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# ------------------
# HOT GAMES SCHEMA
# ------------------

class BGGHotGameBase(BaseModel):
    bgg_id: int
    name: str
    rank: int
    year_published: Optional[int] = None
    image_url: Optional[str] = None
    description: Optional[str] = None
    mechanics: Optional[List[str]] = None
    designers: Optional[List[str]] = None
    artists: Optional[List[str]] = None
    min_players: Optional[int] = None
    max_players: Optional[int] = None
    min_playtime: Optional[int] = None
    max_playtime: Optional[int] = None
    play_time: Optional[int] = None
    min_age: Optional[int] = None
    type: Optional[str] = None
    weight: Optional[float] = None
    average_rating: Optional[float] = None
    bgg_rank: Optional[int] = None
    bgg_link: Optional[str] = None  # ← link generowany na podstawie bgg_id

class BGGHotGameCreate(BGGHotGameBase):
    pass

class BGGHotGameRead(BGGHotGameBase):
    id: int
    last_modified: datetime

    class Config:
        orm_mode = True

# ------------------
# HOT PERSONS SCHEMA
# ------------------

class BGGHotPersonBase(BaseModel):
    bgg_id: int
    name: str
    rank: int
    image_url: Optional[str] = None
    bgg_link: Optional[str] = None  # ← link generowany na podstawie bgg_id

class BGGHotPersonCreate(BGGHotPersonBase):
    pass

class BGGHotPersonRead(BGGHotPersonBase):
    id: int
    last_modified: datetime

    class Config:
        orm_mode = True
