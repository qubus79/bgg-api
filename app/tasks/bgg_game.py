from sqlalchemy import select, text, func, Integer
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.database import AsyncSessionLocal
from app.models.bgg_game import BGGGame
from app.scraper.bgg_game import fetch_bgg_collection
from app.utils.logging import log_info, log_success
from app.database import engine
from app.models.bgg_game import Base
import asyncio

USERNAME = "qubus"

# do testów
from app.utils.logging import log_info, log_success, log_warning, log_error


async def init_bgg_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def setup_scheduler():
    log_info("Scheduler started. Updating BGG collection every 2 hours.")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(update_bgg_collection, IntervalTrigger(hours=2), id="update_bgg_collection_job", replace_existing=True)
    scheduler.start()


async def get_stats():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM bgg_collection"))
        count = result.scalar()

        result2 = await session.execute(text("SELECT MAX(created_at) FROM bgg_collection"))
        last_update = result2.scalar()

        return {
            "count": count or 0,
            "last_update": str(last_update) if last_update else "n/a"
        }


async def update_bgg_collection() -> dict:
    log_info("Inicjalizacja bazy BGG...")
    await init_bgg_db()

    log_info("Rozpoczynam pobieranie danych z BGG kolekcji...")
    await fetch_bgg_collection(USERNAME)

    log_success("🎉 Kolekcja BGG została zsynchronizowana z bazą danych")
    return {"status": "done"}


async def get_bgg_collection() -> list:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(BGGGame))
        return [row.__dict__ for row in result.scalars().all()]

# -----------------------------
# Purchases: lightweight read API
# -----------------------------

async def get_bgg_purchases(limit: int = 500, offset: int = 0) -> dict:
    """Lightweight purchase/acquisition info for the user's BGG collection.

    Includes status flags and type.
    """
    async with AsyncSessionLocal() as session:
        stmt = (
            select(BGGGame)
            .order_by(BGGGame.purchase_acquisition_date.desc().nullslast(), BGGGame.title.asc())
            .limit(limit)
            .offset(offset)
        )
        res = await session.execute(stmt)
        games = res.scalars().all()

        items = []
        for g in games:
            items.append(
                {
                    "bgg_id": g.bgg_id,
                    "title": g.title,
                    "type": g.type,

                    "status_owned": bool(g.status_owned),
                    "status_preordered": bool(g.status_preordered),
                    "status_wishlist": bool(g.status_wishlist),
                    "status_fortrade": bool(g.status_fortrade),
                    "status_prevowned": bool(g.status_prevowned),
                    "status_wanttoplay": bool(g.status_wanttoplay),
                    "status_wanttobuy": bool(g.status_wanttobuy),
                    "status_wishlist_priority": g.status_wishlist_priority,

                    "purchase_price_paid": g.purchase_price_paid,
                    "purchase_currency": g.purchase_currency,
                    "purchase_currency_source": g.purchase_currency_source,
                    "purchase_quantity": g.purchase_quantity,
                    "purchase_acquisition_date": g.purchase_acquisition_date.isoformat() if g.purchase_acquisition_date else None,
                    "purchase_acquired_from": g.purchase_acquired_from,
                    "purchase_private_comment": g.purchase_private_comment,
                }
            )

        return {
            "limit": limit,
            "offset": offset,
            "count": len(items),
            "items": items,
        }


async def _totals_by_currency(session, where_clauses: list) -> list:
    stmt = (
        select(
            BGGGame.purchase_currency.label("currency"),
            func.count().label("count"),
            func.sum(BGGGame.purchase_price_paid).label("total"),
        )
        .where(BGGGame.purchase_price_paid.isnot(None), *where_clauses)
        .group_by(BGGGame.purchase_currency)
        .order_by(BGGGame.purchase_currency.asc().nullslast())
    )
    res = await session.execute(stmt)
    return [
        {
            "currency": row.currency,
            "count": int(row.count or 0),
            "total": float(row.total) if row.total is not None else 0.0,
        }
        for row in res.all()
    ]


async def _totals_by_type_currency(session, where_clauses: list) -> list:
    stmt = (
        select(
            BGGGame.type.label("type"),
            BGGGame.purchase_currency.label("currency"),
            func.count().label("count"),
            func.sum(BGGGame.purchase_price_paid).label("total"),
        )
        .where(BGGGame.purchase_price_paid.isnot(None), *where_clauses)
        .group_by(BGGGame.type, BGGGame.purchase_currency)
        .order_by(func.sum(BGGGame.purchase_price_paid).desc().nullslast())
    )
    res = await session.execute(stmt)
    return [
        {
            "type": row.type,
            "currency": row.currency,
            "count": int(row.count or 0),
            "total": float(row.total) if row.total is not None else 0.0,
        }
        for row in res.all()
    ]


async def get_bgg_purchase_stats() -> dict:
    """Aggregated purchase stats.

    Includes:
    - totals per currency (no FX conversion)
    - counts with/without price
    - status counts
    - type counts
    - value breakdown per status (owned/preordered/wishlist)
      * totals by currency
      * totals by type + currency
    """

    async with AsyncSessionLocal() as session:
        # Totals per currency (overall)
        totals_by_currency = await _totals_by_currency(session, where_clauses=[])

        # Counts (with/without purchase price)
        stmt_counts = select(
            func.count().label("all_games"),
            func.count(BGGGame.purchase_price_paid).label("with_price"),
        )
        res_counts = await session.execute(stmt_counts)
        counts_row = res_counts.one()
        all_games = int(counts_row.all_games or 0)
        with_price = int(counts_row.with_price or 0)
        without_price = all_games - with_price

        # Status counts (owned / preordered / wishlist)
        stmt_status = select(
            func.sum(func.cast(BGGGame.status_owned, Integer)).label("owned"),
            func.sum(func.cast(BGGGame.status_preordered, Integer)).label("preordered"),
            func.sum(func.cast(BGGGame.status_wishlist, Integer)).label("wishlist"),
        )
        res_status = await session.execute(stmt_status)
        status_row = res_status.one()

        status_counts = {
            "owned": int(status_row.owned or 0),
            "preordered": int(status_row.preordered or 0),
            "wishlist": int(status_row.wishlist or 0),
        }

        # Type counts
        stmt_type = (
            select(BGGGame.type.label("type"), func.count().label("count"))
            .group_by(BGGGame.type)
            .order_by(func.count().desc())
        )
        res_type = await session.execute(stmt_type)
        type_counts = [
            {"type": row.type, "count": int(row.count or 0)}
            for row in res_type.all()
        ]

        # Value breakdown per status + currency, and per status + type + currency
        status_breakdown = {
            "owned": {
                "totals_by_currency": await _totals_by_currency(session, [BGGGame.status_owned.is_(True)]),
                "totals_by_type_currency": await _totals_by_type_currency(session, [BGGGame.status_owned.is_(True)]),
            },
            "preordered": {
                "totals_by_currency": await _totals_by_currency(session, [BGGGame.status_preordered.is_(True)]),
                "totals_by_type_currency": await _totals_by_type_currency(session, [BGGGame.status_preordered.is_(True)]),
            },
            "wishlist": {
                "totals_by_currency": await _totals_by_currency(session, [BGGGame.status_wishlist.is_(True)]),
                "totals_by_type_currency": await _totals_by_type_currency(session, [BGGGame.status_wishlist.is_(True)]),
            },
        }

        return {
            "counts": {
                "all_games": all_games,
                "with_price": with_price,
                "without_price": without_price,
            },
            "status_counts": status_counts,
            "type_counts": type_counts,
            "totals_by_currency": totals_by_currency,
            "status_breakdown": status_breakdown,
        }
