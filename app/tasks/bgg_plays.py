import os
import asyncio
from datetime import date, datetime, timedelta

from sqlalchemy import select, text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.database import AsyncSessionLocal, engine, Base
from app.models.bgg_plays import BGGPlay
from app.scraper.bgg_plays import update_bgg_plays_from_collection
from app.utils.logging import log_info, log_success, log_warning


# Jak często synchronizować plays (domyślnie co 6h, bo to cięższe niż kolekcja)
PLAYS_SYNC_HOURS = int(os.getenv("BGG_PLAYS_SYNC_HOURS", "6"))


async def init_plays_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def setup_plays_scheduler():
    log_info(f"Scheduler started. Updating BGG plays every {PLAYS_SYNC_HOURS} hours.")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        update_bgg_plays,
        IntervalTrigger(hours=PLAYS_SYNC_HOURS),
        id="update_bgg_plays_job",
        replace_existing=True,
    )
    scheduler.start()


async def get_plays_stats():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM bgg_plays"))
        count = result.scalar()

        result2 = await session.execute(text("SELECT MAX(updated_at) FROM bgg_plays"))
        last_update = result2.scalar()

        return {
            "count": int(count or 0),
            "last_update": str(last_update) if last_update else "n/a",
        }


async def update_bgg_plays() -> dict:
    """
    Sync plays for games that exist in our collection DB (cross-reference by bgg_id).
    Uses authenticated cookies via existing auth mechanism inside the scraper.
    """
    log_info("Inicjalizacja bazy BGG Plays...")
    await init_plays_db()

    log_info("Rozpoczynam pobieranie plays z BGG (na podstawie gier w kolekcji DB)...")
    result = await update_bgg_plays_from_collection()

    log_success("🎉 Plays z BGG zostały zsynchronizowane z bazą danych")
    return {"status": "done", **(result or {})}


def _model_to_dict(obj) -> dict:
    d = dict(obj.__dict__)
    d.pop("_sa_instance_state", None)
    return d


async def get_bgg_plays(limit: int = 2000, offset: int = 0, bgg_id: int | None = None) -> list:
    """
    Read plays from DB.
    Optional filter: bgg_id == object_id.
    """
    async with AsyncSessionLocal() as session:
        stmt = select(BGGPlay).order_by(
            BGGPlay.play_date.desc().nullslast(),
            BGGPlay.tstamp.desc().nullslast(),
        )

        if bgg_id is not None:
            stmt = stmt.where(BGGPlay.object_id == int(bgg_id))

        stmt = stmt.limit(limit).offset(offset)

        result = await session.execute(stmt)
        return [_model_to_dict(row) for row in result.scalars().all()]
    

# -----------------------------------------------------------------------------
# Chart-oriented / aggregated endpoints helpers
# -----------------------------------------------------------------------------

def _iso_date(value: str | None) -> date | None:
    """Parse YYYY-MM-DD into date. Returns None if value is falsy."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _date_window(days: int = 365, start: str | None = None, end: str | None = None) -> tuple[date, date]:
    """Build inclusive [start, end] date window."""
    end_d = _iso_date(end) or date.today()
    start_d = _iso_date(start) or (end_d - timedelta(days=max(1, int(days))))
    return start_d, end_d


def _safe_granularity(granularity: str) -> str:
    """Allow only safe date_trunc granularities."""
    g = (granularity or "week").lower().strip()
    if g not in {"day", "week", "month", "quarter"}:
        return "week"
    return g


async def get_plays_summary(
    bgg_id: int | None = None,
    days: int = 365,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """High-level plays summary for overview tiles (optionally per-game)."""
    start_d, end_d = _date_window(days=days, start=start, end=end)

    where = "WHERE play_date IS NOT NULL AND play_date >= :start_d AND play_date <= :end_d"
    params: dict = {"start_d": start_d, "end_d": end_d}
    if bgg_id is not None:
        where += " AND object_id = :bgg_id"
        params["bgg_id"] = int(bgg_id)

    sql_totals = text(
        f"""
        SELECT
            COUNT(*)::int AS plays,
            COUNT(DISTINCT object_id)::int AS distinct_games,
            COALESCE(SUM(COALESCE(quantity, 1)), 0)::int AS total_quantity,
            COALESCE(SUM(COALESCE(length, 0)), 0)::int AS total_minutes,
            MAX(play_date) AS last_play_date
        FROM bgg_plays
        {where}
        """
    )

    sql_top_locations = text(
        f"""
        SELECT
            COALESCE(NULLIF(TRIM(location), ''), 'Unknown') AS location,
            COUNT(*)::int AS plays
        FROM bgg_plays
        {where}
        GROUP BY 1
        ORDER BY plays DESC, location ASC
        LIMIT 10
        """
    )

    sql_weekday = text(
        f"""
        SELECT
            EXTRACT(DOW FROM play_date)::int AS dow,
            COUNT(*)::int AS plays
        FROM bgg_plays
        {where}
        GROUP BY 1
        ORDER BY dow ASC
        """
    )

    async with AsyncSessionLocal() as session:
        totals_row = (await session.execute(sql_totals, params)).mappings().first() or {}
        locations = (await session.execute(sql_top_locations, params)).mappings().all()
        weekday = (await session.execute(sql_weekday, params)).mappings().all()

    # Normalize weekday to 0..6 (Sun..Sat) with zero-fill
    weekday_map = {int(r["dow"]): int(r["plays"]) for r in weekday if r.get("dow") is not None}
    weekday_series = [{"dow": d, "plays": weekday_map.get(d, 0)} for d in range(0, 7)]

    return {
        "window": {"start": str(start_d), "end": str(end_d)},
        "filter": {"bgg_id": int(bgg_id) if bgg_id is not None else None},
        "totals": {
            "plays": int(totals_row.get("plays") or 0),
            "distinct_games": int(totals_row.get("distinct_games") or 0),
            "total_quantity": int(totals_row.get("total_quantity") or 0),
            "total_minutes": int(totals_row.get("total_minutes") or 0),
            "last_play_date": str(totals_row.get("last_play_date")) if totals_row.get("last_play_date") else None,
        },
        "top_locations": [{"location": r["location"], "plays": int(r["plays"])} for r in locations],
        "weekday": weekday_series,
    }


async def get_plays_series(
    metric: str = "plays",
    granularity: str = "week",
    bgg_id: int | None = None,
    days: int = 365,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Time-series for charts.

    metric:
      - plays: count of rows
      - quantity: sum(quantity or 1)
      - minutes: sum(length)
    granularity: day|week|month|quarter
    """
    start_d, end_d = _date_window(days=days, start=start, end=end)
    g = _safe_granularity(granularity)

    metric_key = (metric or "plays").lower().strip()
    if metric_key == "minutes":
        metric_sql = "COALESCE(SUM(COALESCE(length, 0)), 0)::int"
    elif metric_key == "quantity":
        metric_sql = "COALESCE(SUM(COALESCE(quantity, 1)), 0)::int"
    else:
        metric_key = "plays"
        metric_sql = "COUNT(*)::int"

    where = "WHERE play_date IS NOT NULL AND play_date >= :start_d AND play_date <= :end_d"
    params: dict = {"start_d": start_d, "end_d": end_d}
    if bgg_id is not None:
        where += " AND object_id = :bgg_id"
        params["bgg_id"] = int(bgg_id)

    sql = text(
        f"""
        SELECT
            date_trunc('{g}', play_date)::date AS period,
            {metric_sql} AS value
        FROM bgg_plays
        {where}
        GROUP BY 1
        ORDER BY period ASC
        """
    )

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sql, params)).mappings().all()

    return {
        "window": {"start": str(start_d), "end": str(end_d)},
        "filter": {"bgg_id": int(bgg_id) if bgg_id is not None else None},
        "series": [{"period": str(r["period"]), "value": int(r["value"])} for r in rows],
        "metric": metric_key,
        "granularity": g,
    }


async def get_plays_breakdown(
    kind: str,
    bgg_id: int | None = None,
    days: int = 365,
    start: str | None = None,
    end: str | None = None,
    limit: int = 20,
) -> dict:
    """Generic categorical breakdown for donut/bar charts.

    kind:
      - location
      - num_players
      - weekday
      - game (top games)
    """
    start_d, end_d = _date_window(days=days, start=start, end=end)

    where = "WHERE play_date IS NOT NULL AND play_date >= :start_d AND play_date <= :end_d"
    params: dict = {"start_d": start_d, "end_d": end_d, "limit": int(limit)}
    if bgg_id is not None:
        where += " AND object_id = :bgg_id"
        params["bgg_id"] = int(bgg_id)

    k = (kind or "location").lower().strip()

    if k == "num_players":
        sql = text(
            f"""
            SELECT
                COALESCE(num_players, 0)::int AS key,
                COUNT(*)::int AS value
            FROM bgg_plays
            {where}
            GROUP BY 1
            ORDER BY value DESC, key ASC
            LIMIT :limit
            """
        )
    elif k == "weekday":
        sql = text(
            f"""
            SELECT
                EXTRACT(DOW FROM play_date)::int AS key,
                COUNT(*)::int AS value
            FROM bgg_plays
            {where}
            GROUP BY 1
            ORDER BY key ASC
            """
        )
    elif k == "game":
        sql = text(
            f"""
            SELECT
                object_id::int AS key,
                COALESCE(NULLIF(TRIM(game_name), ''), 'Unknown') AS label,
                COUNT(*)::int AS value
            FROM bgg_plays
            {where}
            GROUP BY 1, 2
            ORDER BY value DESC, label ASC
            LIMIT :limit
            """
        )
    else:
        k = "location"
        sql = text(
            f"""
            SELECT
                COALESCE(NULLIF(TRIM(location), ''), 'Unknown') AS key,
                COUNT(*)::int AS value
            FROM bgg_plays
            {where}
            GROUP BY 1
            ORDER BY value DESC, key ASC
            LIMIT :limit
            """
        )

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sql, params)).mappings().all()

    items: list[dict] = []
    for r in rows:
        # rows may contain label (for kind=game)
        item = {"key": r.get("key"), "value": int(r.get("value") or 0)}
        if "label" in r:
            item["label"] = r.get("label")
        items.append(item)

    return {
        "window": {"start": str(start_d), "end": str(end_d)},
        "filter": {"bgg_id": int(bgg_id) if bgg_id is not None else None},
        "kind": k,
        "items": items,
    }


async def get_plays_heatmap(
    bgg_id: int | None = None,
    days: int = 365,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Heatmap counts by weekday x hour (from tstamp)."""
    start_d, end_d = _date_window(days=days, start=start, end=end)

    where = "WHERE play_date IS NOT NULL AND play_date >= :start_d AND play_date <= :end_d"
    params: dict = {"start_d": start_d, "end_d": end_d}
    if bgg_id is not None:
        where += " AND object_id = :bgg_id"
        params["bgg_id"] = int(bgg_id)

    sql = text(
        f"""
        SELECT
            EXTRACT(DOW FROM play_date)::int AS dow,
            COALESCE(EXTRACT(HOUR FROM tstamp), 0)::int AS hour,
            COUNT(*)::int AS plays
        FROM bgg_plays
        {where}
        GROUP BY 1, 2
        ORDER BY dow ASC, hour ASC
        """
    )

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sql, params)).mappings().all()

    # Build 7x24 matrix with zero-fill
    matrix = [[0 for _ in range(24)] for __ in range(7)]
    for r in rows:
        d = int(r.get("dow") or 0)
        h = int(r.get("hour") or 0)
        if 0 <= d <= 6 and 0 <= h <= 23:
            matrix[d][h] = int(r.get("plays") or 0)

    return {
        "window": {"start": str(start_d), "end": str(end_d)},
        "filter": {"bgg_id": int(bgg_id) if bgg_id is not None else None},
        "matrix": matrix,
        "axis": {"dow": list(range(0, 7)), "hour": list(range(0, 24))},
    }


async def get_plays_stats_per_game():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGPlay))
        plays = result.scalars().all()

    stats = {}

    for p in plays:
        gid = p.object_id
        if gid not in stats:
            stats[gid] = {
                "bgg_id": gid,
                "game_name": p.game_name,
                "plays": 0,
                "total_quantity": 0,
                "wins": 0,
            }

        stats[gid]["plays"] += 1
        stats[gid]["total_quantity"] += p.quantity or 1

        if p.players:
            for pl in p.players:
                if pl.get("username") and pl.get("win") in ("1", 1, True):
                    stats[gid]["wins"] += 1

    return list(stats.values())


async def get_plays_stats_per_player():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGPlay))
        plays = result.scalars().all()

    stats = {}

    for p in plays:
        if not p.players:
            continue

        for pl in p.players:
            key = pl.get("username") or pl.get("name")
            if not key:
                continue

            if key not in stats:
                stats[key] = {
                    "player": key,
                    "plays": 0,
                    "wins": 0,
                }

            stats[key]["plays"] += 1
            if pl.get("win") in ("1", 1, True):
                stats[key]["wins"] += 1

    return list(stats.values())


async def get_my_plays_stats(username: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGPlay))
        plays = result.scalars().all()

    total = 0
    wins = 0

    for p in plays:
        if not p.players:
            continue

        for pl in p.players:
            if pl.get("username") == username:
                total += 1
                if pl.get("win") in ("1", 1, True):
                    wins += 1

    return {
        "username": username,
        "plays": total,
        "wins": wins,
    }