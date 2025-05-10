# app/schemas/bgg_hotness.py
from pydantic import BaseModel
from typing import Optional
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

class BGGHotPersonCreate(BGGHotPersonBase):
    pass

class BGGHotPersonRead(BGGHotPersonBase):
    id: int
    last_modified: datetime

    class Config:
        orm_mode = True
