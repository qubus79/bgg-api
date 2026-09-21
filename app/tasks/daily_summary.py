# app/tasks/daily_summary.py
"""Harmonogram dziennego podsumowania.

Sama treść i wysyłka siedzą w `app/utils/daily_summary.py` (plik wspólny dla
trzech serwisów); tutaj zostaje tylko wpięcie w APScheduler, zgodnie z układem
pozostałych zadań tego repo.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.utils.daily_summary import SUMMARY_HOUR, TIMEZONE, schedule_entry
from app.utils.logging import log_info


async def setup_daily_summary_scheduler():
    log_info(f"🕒 Podsumowanie dnia zaplanowane na {SUMMARY_HOUR}:00 Europe/Warsaw.")
    scheduler = AsyncIOScheduler()
    # Czas polski, bo doba ma się kończyć tam, gdzie kończy się dzień
    # użytkownika. `misfire_grace_time` ratuje wysyłkę, gdy proces akurat
    # wstawał o tej godzinie — harmonogram żyje w procesie web.
    scheduler.add_job(
        schedule_entry,
        CronTrigger(hour=SUMMARY_HOUR, minute=0, timezone=TIMEZONE),
        id="daily_summary_job",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
