from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.database import Base


class RemovedRow(Base):
    """Ślad usuniętego wiersza (gra z kolekcji, akcesorium) — `/…/changes`
    podaje go jako `removed`, żeby telefon pobierający same zmiany też go
    usunął. Ślady starsze niż `app.changes.REMOVED_KEEP` są kasowane."""

    __tablename__ = "removed_rows"

    kind = Column(String, primary_key=True)        # "game" | "accessory"
    key = Column(Integer, primary_key=True)        # bgg_id
    removed_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
