import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def _format_list(title: str, items: List[str], limit: int = 8) -> str:
    if not items:
        return ""
    displayed = items[:limit]
    remainder = len(items) - len(displayed)
    lines = "\n".join(f"- {item}" for item in displayed)
    if remainder > 0:
        lines += f"\n- and {remainder} more..."
    return f"\n*{title}*\n{lines}\n"


async def send_scrape_message(
    scraper_name: str,
    status: str,
    start_time: datetime,
    end_time: datetime,
    stats: Dict[str, int],
    lists: Dict[str, List[str]],
    notes: Optional[str] = None,
) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        return

    duration = end_time - start_time
    lines = []
    lines.append(f"*{scraper_name}* — {status}")
    lines.append(f"🕒 Start: {start_time.isoformat()} | End: {end_time.isoformat()} | Δ {duration}")
    if notes:
        lines.append(notes)
    lines.append("\n*Stats*")
    stats_lines = []
    for key, value in stats.items():
        stats_lines.append(f"{key}: {value}")
    lines.append(" | ".join(stats_lines))
    for title, items in lists.items():
        lines.append(_format_list(title, items))

    payload = {
        "chat_id": CHAT_ID,
        "text": "\n".join(filter(None, lines)),
        "parse_mode": "Markdown",
    }

    async with httpx.AsyncClient() as client:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        try:
            await client.post(url, json=payload)
        except httpx.HTTPError:
            pass
