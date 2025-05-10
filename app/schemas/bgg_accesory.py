# schemas/bgg_accessory.py
from pydantic import BaseModel
from typing import Optional

class BGGAccessoryBase(BaseModel):
    bgg_id: int
    name: str
    year_published: Optional[int] = None
    image: Optional[str] = None
    description: Optional[str] = None
    publisher: Optional[str] = None

    owned: bool = False
    preordered: bool = False
    wishlist: bool = False
    want_to_buy: bool = False
    want_to_play: bool = False
    want: bool = False
    for_trade: bool = False
    previously_owned: bool = False

    num_plays: int = 0
    my_rating: float = 0
    average_rating: float = 0
    bgg_rank: int = 0

    last_modified: Optional[str] = None

class BGGAccessoryCreate(BGGAccessoryBase):
    pass

class BGGAccessoryRead(BGGAccessoryBase):
    id: int

    class Config:
        orm_mode = True
