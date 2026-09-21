# app/jobs_registry.py
"""Rejestracja zadań uruchamianych z aplikacji (POST /jobs/{job}/run)."""

from app import jobs
from app.tasks import bgg_accessory, bgg_game, bgg_hotness, bgg_plays
from app.utils.daily_summary import run_daily_summary


def register_jobs() -> None:
    jobs.register(
        "daily_summary",
        run_daily_summary,
        label="Podsumowanie dnia",
        stage_count=1,
    )
    jobs.register(
        "bgg_collection",
        bgg_game.update_bgg_collection,
        label="Kolekcja BGG",
        last_update_fn=bgg_game.get_stats,
        stage_count=2,
    )
    jobs.register(
        "bgg_plays",
        bgg_plays.update_bgg_plays,
        label="Rozgrywki (plays)",
        last_update_fn=bgg_plays.get_plays_stats,
        stage_count=2,
    )
    jobs.register(
        "bgg_accessories",
        bgg_accessory.update_bgg_accessories,
        label="Akcesoria",
        last_update_fn=bgg_accessory.get_accessory_stats,
        stage_count=2,
    )
    jobs.register(
        "bgg_hotness_games",
        bgg_hotness.update_hot_games,
        label="Hotness — gry",
        last_update_fn=bgg_hotness.get_hotness_game_stats,
        stage_count=2,
    )
    jobs.register(
        "bgg_hotness_persons",
        bgg_hotness.update_hot_persons,
        label="Hotness — osoby",
        last_update_fn=bgg_hotness.get_hotness_person_stats,
        stage_count=2,
    )
