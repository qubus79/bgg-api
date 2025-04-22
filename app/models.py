from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.database import Base

class BGGCollectionItem(Base):
    __tablename__ = "bgg_collection"

    id = Column(Integer, primary_key=True, index=True)
    bgg_id = Column(Integer, index=True, unique=True)  # ID z BoardGameGeek
    title = Column(String, index=True)
    year_published = Column(Integer, nullable=True)
    image = Column(String, nullable=True)
    thumbnail = Column(String, nullable=True)
    num_plays = Column(Integer, nullable=True)

    status_owned = Column(Boolean, default=False)
    status_preordered = Column(Boolean, default=False)
    status_wishlist = Column(Boolean, default=False)
    status_fortrade = Column(Boolean, default=False)
    status_prevowned = Column(Boolean, default=False)
    status_wanttoplay = Column(Boolean, default=False)
    status_wanttobuy = Column(Boolean, default=False)
    status_wishlist_priority = Column(Integer, nullable=True)

    my_rating = Column(Float, nullable=True)
    average_rating = Column(Float, nullable=True)
    bgg_rank = Column(Integer, nullable=True)

    min_players = Column(Integer, nullable=True)
    max_players = Column(Integer, nullable=True)
    min_playtime = Column(Integer, nullable=True)
    max_playtime = Column(Integer, nullable=True)
    play_time = Column(Integer, nullable=True)
    min_age = Column(Integer, nullable=True)

    description = Column(Text, nullable=True)
    original_title = Column(String, nullable=True)
    type = Column(String, nullable=True)

    mechanics = Column(JSONB, nullable=True)
    designers = Column(JSONB, nullable=True)
    artists = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=False), server_default=func.now())
