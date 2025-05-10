# app/schemas.py
from pydantic import BaseModel
from typing import Optional

class InterestLevelUpdate(BaseModel):
    interest_level: Optional[str]
