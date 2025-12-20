from fastapi import APIRouter, Query

from app.tasks import bgg_plays

router = APIRouter(prefix="/bgg_plays", tags=["BGG Plays"])


@router.post("/update")
async def update_bgg_plays():
    """Trigger a sync of BGG plays for all games currently in the DB collection."""
    return await bgg_plays.update_bgg_plays()


@router.get("")
async def list_bgg_plays(
    limit: int = Query(2000, ge=1, le=20000),
    offset: int = Query(0, ge=0),
    bgg_id: int | None = Query(None, description="Filter plays by game BGG id (object_id)"),
):
    """List plays from DB. Optionally filter by game BGG id."""
    items = await bgg_plays.get_bgg_plays(limit=limit, offset=offset, bgg_id=bgg_id)
    return {
        "limit": limit,
        "offset": offset,
        "count": len(items),
        "items": items,
    }


@router.get("/stats")
async def plays_stats():
    """Basic stats for stored plays."""
    return await bgg_plays.get_plays_stats()


@router.get("/stats/games")
async def plays_stats_per_game():
    """Stats aggregated per game."""
    return await bgg_plays.get_plays_stats_per_game()


@router.get("/stats/players")
async def plays_stats_per_player():
    """Stats aggregated per player."""
    return await bgg_plays.get_plays_stats_per_player()


@router.get("/stats/me")
async def my_plays_stats(username: str = "qubus"):
    """Stats for the current user (by BGG username)."""
    return await bgg_plays.get_my_plays_stats(username)