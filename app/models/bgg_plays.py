# app/models/bgg_play.py

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base


class BGGPlay(Base):
    """A single logged play fetched from BGG's internal plays JSON endpoint.

    Source endpoint (requires authenticated cookies):
    https://boardgamegeek.com/geekplay.php?action=getplays&ajax=1&currentUser=true&objectid={bggId}&objecttype=thing&pageID=1&showcount=200

    Notes:
    - `play_id` is the unique identifier from BGG (`playid`).
    - `object_id` corresponds to the BGG game id (`objectid`) and can be cross-referenced with `bgg_collection.bgg_id`.
    - We store dates/timestamps as strings to avoid format issues and keep parity with BGG output.
    - `players` stores the list of players (including score, win, new, userid, username) as JSON.
    - `raw` stores the raw play object for future-proofing.
    """

    __tablename__ = "bgg_plays"

    id = Column(Integer, primary_key=True, index=True)

    # BGG identifiers
    play_id = Column(Integer, unique=True, index=True, nullable=False)  # playid
    object_id = Column(Integer, index=True, nullable=False)  # objectid (BGG game id)
    object_type = Column(String, nullable=True)  # usually "thing"

    # user context (BGG response includes userid; currentUser=true ties to logged-in user)
    user_id = Column(Integer, nullable=True)
    username = Column(String, nullable=True)

    # play fields
    play_date = Column(String, nullable=True)  # playdate, e.g. "2025-12-17"
    tstamp = Column(String, nullable=True)  # e.g. "2025-12-15 15:28:04"
    length_ms = Column(Integer, nullable=True)  # BGG provides length_ms as string
    location = Column(String, nullable=True)
    length = Column(Integer, nullable=True)  # minutes
    quantity = Column(Integer, nullable=True)  # number of plays for that date
    num_players = Column(Integer, nullable=True)
    comments_value = Column(Text, nullable=True)
    comments_rendered = Column(Text, nullable=True)

    # NOTE: BGG sends many flags as strings ("0"/"1"); the scraper should normalize to bool/int.
    incomplete = Column(Boolean, nullable=True)
    now_in_stats = Column(Boolean, nullable=True)  # nowinstats
    win_state = Column(String, nullable=True)  # winstate
    online = Column(Boolean, nullable=True)

    # convenience: game name echoed by the plays endpoint (optional)
    game_name = Column(String, nullable=True)  # name

    # nested
    players = Column(JSONB, nullable=True)
    subtypes = Column(JSONB, nullable=True)  # list of {"subtype": ...}

    # raw payload for forward compatibility
    raw = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())