import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


LIST_ICONS: dict[str, str] = {
    "Added games": "🧺",
    "Updated games": "♻️",
    "Removed games": "🗑️",
    "Added accessories": "🧩",
    "Updated accessories": "🪄",
    "Removed accessories": "🧹",
    "Top games": "🔥",
    "Top persons": "🌟",
    "New plays": "🆕",
    "Updated plays": "🔄",
}


def _format_list(title: str, items: List[str], limit: int = 8) -> str:
    if not items:
        return ""
    displayed = items[:limit]
    remainder = len(items) - len(displayed)
    bullet = LIST_ICONS.get(title, "•")
    lines = "\n".join(f"{bullet} {item}" for item in displayed)
    if remainder > 0:
        lines += f"\n{bullet} and {remainder} more..."
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
    clean_duration = str(duration).split(".")[0]
    time_format = "%Y-%m-%d %H:%M:%S"
    lines = []
    lines.append(f"🎯 *{scraper_name}* — {status}")
    lines.append("")
    lines.append(f"🟢 Start: *{start_time.strftime(time_format)}*")
    lines.append(f"🔴 End: *{end_time.strftime(time_format)}*")
    lines.append(f"⏱️ Duration: *{clean_duration}*")
    if notes:
        lines.append("")
        lines.append(f"💬 {notes}")
    lines.append("")
    STAT_ICONS: dict[str, str] = {
        "Total games": "🎲",
        "Added": "🧺",
        "Updated": "♻️",
        "Removed": "🗑️",
        "Total accessories": "🪄",
        "Hot games": "🔥",
        "Hot persons": "🌟",
        "Hash skips": "🚫",
        "Detail hash updates": "🔍",
        "Plays processed": "📊",
        "New plays": "🆕",
        "Updated plays": "🔄",
    }
    lines.append("*Stats*")
    stats_lines = []
    for key, value in stats.items():
        icon = STAT_ICONS.get(key, "•")
        stats_lines.append(f"{icon} {key}: *{value}*")
    if stats_lines:
        lines.extend(stats_lines)
    lines.append("")
    for title, items in lists.items():
        list_block = _format_list(title, items)
        if list_block:
            lines.append(list_block)

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
