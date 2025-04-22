# app/models.py
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.database import Base

class BGGCollectionItem(Base):
    __tablename__ = "bgg_collection"

    id = Column(Integer, primary_key=True, index=True)
    bgg_id = Column(Integer, index=True)
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

    stats = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
