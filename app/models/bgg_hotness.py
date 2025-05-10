# app/models/bgg_hotness.py

from sqlalchemy import Column, Integer, String, DateTime, Text, Float
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from app.database import Base

class BGGHotGame(Base):
    __tablename__ = "bgg_hot_games"

    id = Column(Integer, primary_key=True, index=True)
    bgg_id = Column(Integer, unique=True, nullable=False)
    rank = Column(Integer, nullable=True)
    name = Column(String, nullable=True)
    original_title = Column(String, nullable=True)
    year_published = Column(Integer, nullable=True)
    image = Column(String, nullable=True)
    bgg_url = Column(String, nullable=True)
    description = Column(Text, nullable=True)

    min_players = Column(Integer, nullable=True)
    max_players = Column(Integer, nullable=True)
    min_playtime = Column(Integer, nullable=True)
    max_playtime = Column(Integer, nullable=True)
    play_time = Column(Integer, nullable=True)
    min_age = Column(Integer, nullable=True)
    weight = Column(Float, nullable=True)

    mechanics = Column(JSONB, nullable=True)
    designers = Column(JSONB, nullable=True)
    artists = Column(JSONB, nullable=True)
    type = Column(String, nullable=True)

    last_modified = Column(DateTime, default=datetime.utcnow)


# -----------------------------
# MODEL: BGG Hotness Person
# -----------------------------
class BGGHotPerson(Base):
    __tablename__ = "bgg_hot_persons"

    id = Column(Integer, primary_key=True, index=True)
    bgg_id = Column(Integer, unique=True, nullable=False)
    rank = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    image = Column(String, nullable=True)
    bgg_url = Column(String, nullable=True)
    last_modified = Column(DateTime, default=datetime.utcnow)
