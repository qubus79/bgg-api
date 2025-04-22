# app/schemas.py
from pydantic import BaseModel
from typing import Optional

class Stats(BaseModel):
    count: int
    last_update: str

class InterestLevelUpdate(BaseModel):
    interest_level: Optional[str]
