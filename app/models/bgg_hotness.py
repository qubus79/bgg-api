# app/models/bgg_hotness.py

from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.orm import declarative_base
from datetime import datetime
from app.database import Base

# -----------------------------
# MODEL: BGG Hotness Game
# -----------------------------
class BGGHotGame(Base):
    __tablename__ = "bgg_hot_games"

    id = Column(Integer, primary_key=True, index=True)
    bgg_id = Column(Integer, unique=True, nullable=False)
    rank = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    year_published = Column(Integer, nullable=True)
    image = Column(String, nullable=True)
    mechanics = Column(Text, nullable=True)
    designers = Column(Text, nullable=True)
    artists = Column(Text, nullable=True)
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
    last_modified = Column(DateTime, default=datetime.utcnow)
