from typing import Literal

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

# -----------------------------
# Charts (aggregations for UI)
# -----------------------------

@router.get("/summary")
async def plays_summary(
    days: int = Query(365, ge=1, le=3650, description="How many trailing days to include"),
    username: str = Query("qubus", description="BGG username used for 'me' context (wins, etc.)"),
    object_id: int | None = Query(None, description="Optional: filter to a single game (BGG id / object_id)"),
):
    """Pack of lightweight play stats for overview screens (single request for the UI)."""
    return await bgg_plays.get_plays_summary(days=days, username=username, object_id=object_id)


@router.get("/series")
async def plays_series(
    days: int = Query(365, ge=1, le=3650, description="How many trailing days to include"),
    bucket: Literal["day", "week", "month"] = Query("week", description="Time bucketing for the X axis"),
    metric: Literal["plays", "minutes", "quantity"] = Query(
        "plays", description="Y value: plays=count, minutes=sum(length_ms)/60000, quantity=sum(quantity)"
    ),
    group_by: Literal["none", "location", "game", "players_count", "online"] = Query(
        "none", description="Optional series split (multiple lines)"
    ),
    top: int = Query(5, ge=1, le=25, description="When group_by=game, how many top games to include"),
    object_id: int | None = Query(None, description="Optional: filter to a single game (BGG id / object_id)"),
):
    """Time series for chart #1/#2/#7 (and variants)."""
    return await bgg_plays.get_plays_series(
        days=days,
        bucket=bucket,
        metric=metric,
        group_by=group_by,
        top=top,
        object_id=object_id,
    )


@router.get("/breakdown")
async def plays_breakdown(
    days: int = Query(365, ge=1, le=3650, description="How many trailing days to include"),
    by: Literal["weekday", "location", "players", "length_bucket", "game", "win_rate"] = Query(
        "weekday", description="Aggregation dimension for bar/donut charts"
    ),
    limit: int = Query(20, ge=1, le=100, description="Max items returned for leaderboards"),
    username: str = Query("qubus", description="BGG username used for win_rate aggregation"),
    object_id: int | None = Query(None, description="Optional: filter to a single game (BGG id / object_id)"),
):
    """Generic breakdown endpoint for chart #3/#4/#5/#6 (and variants)."""
    return await bgg_plays.get_plays_breakdown(
        days=days,
        by=by,
        limit=limit,
        username=username,
        object_id=object_id,
    )


@router.get("/heatmap")
async def plays_heatmap(
    days: int = Query(365, ge=1, le=3650, description="How many trailing days to include"),
    object_id: int | None = Query(None, description="Optional: filter to a single game (BGG id / object_id)"),
):
    """Heatmap data for chart #3 (date x weekday / activity map)."""
    return await bgg_plays.get_plays_heatmap(days=days, object_id=object_id)