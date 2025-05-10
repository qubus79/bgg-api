from sqlalchemy import Column, Integer, String, Boolean, Float
from app.database import Base

class BGGAccessory(Base):
    __tablename__ = "bgg_accessories"

    id = Column(Integer, primary_key=True, index=True)
    bgg_id = Column(Integer, unique=True, index=True)
    name = Column(String)
    year_published = Column(Integer, nullable=True)
    image = Column(String, nullable=True)
    description = Column(String, nullable=True)
    publisher = Column(String, nullable=True)

    owned = Column(Boolean, default=False)
    preordered = Column(Boolean, default=False)
    wishlist = Column(Boolean, default=False)
    want_to_buy = Column(Boolean, default=False)
    want_to_play = Column(Boolean, default=False)
    want = Column(Boolean, default=False)
    for_trade = Column(Boolean, default=False)
    previously_owned = Column(Boolean, default=False)

    num_plays = Column(Integer, default=0)
    my_rating = Column(Float, default=0)
    average_rating = Column(Float, default=0)
    bgg_rank = Column(Integer, default=0)

    last_modified = Column(String)
