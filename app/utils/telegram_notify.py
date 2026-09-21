import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from app.utils.logging import log_error

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Nazwa serwisu w treści powiadomień — trzy usługi piszą na ten sam czat.
SERVICE_NAME = "bgg-api"

# Sukcesy domyślnie milczą; od raportowania jest dzienne podsumowanie.
# Ustawienie na "true" przywraca wiadomość po każdym przebiegu.
NOTIFY_SUCCESS = os.getenv("TELEGRAM_NOTIFY_SUCCESS", "false").lower() == "true"

# Twardy limit Telegrama na długość wiadomości.
TELEGRAM_LIMIT = 4096

# Ikony przy wypunktowaniach i przy statystykach. Jeden zestaw dla trzech
# serwisów — klucze się nie pokrywają, więc unia niczego nie nadpisuje, a plik
# może zostać identyczną kopią.
LIST_ICONS: dict[str, str] = {
    # premiery
    "Added premieres": "🧺",
    "Updated premieres": "♻️",
    "Removed premieres": "🧹",
    # kolekcja i akcesoria BGG
    "Added games": "🧺",
    "Updated games": "♻️",
    "Removed games": "🧹",
    "Added accessories": "🧩",
    "Updated accessories": "🪄",
    "Removed accessories": "🧹",
    "Top games": "🔥",
    "Top persons": "🌟",
    "New plays": "🆕",
    "Updated plays": "🔄",
    # koszulki
    "Added sleeves": "🆕",
    "Updated sleeves": "♻️",
    "Removed games (sleeves)": "🧹",
    "API errors": "⚠️",
}

STAT_ICONS: dict[str, str] = {
    # Etykiety dziennego podsumowania — te idą na Telegram zawsze.
    "W katalogu": "📦",
    "Przejrzane": "🔍",
    "Sparsowane": "📄",
    "Dodane": "🧺",
    "Zaktualizowane": "♻️",
    "Usunięte": "🧹",
    "Oznaczone jako nieaktywne": "💤",
    "Pobrane okładki": "🖼️",
    "Błędy": "⚠️",
    "Nieudane przebiegi": "❌",
    # The Shelf — przebieg niepełny (wysyłany mimo wyciszenia sukcesów).
    "Scanned channels": "🔍",
    "Parsed channels": "📄",
    "Failed channels": "⚠️",
    "Added campaigns": "🧺",
    "Updated campaigns": "♻️",
    "Marked inactive": "💤",
    # Etykiety wiadomości po pojedynczym przebiegu (TELEGRAM_NOTIFY_SUCCESS).
    "Added premieres": "🧺",
    "Updated premieres": "♻️",
    "Skipped premieres": "🗑️",
    "Removed premieres": "🧹",
    "Scraped total": "🎯",
    "Total games": "🎲",
    "Added": "🧺",
    "Updated": "♻️",
    "Removed": "🗑️",
    "Total accessories": "🪄",
    "Unchanged accessories": "✅",
    "Hot games": "🔥",
    "Hot persons": "🌟",
    "Hash skips": "🚫",
    "Detail hash updates": "🔍",
    "Plays processed": "📊",
    "Skipped plays": "🗑️",
    "New plays": "🆕",
    "Updated plays": "🔄",
    "Processed games": "🎯",
    "Removed games (sleeves)": "🧹",
    "Added sleeves": "🆕",
    "Updated sleeves": "♻️",
    "Unchanged sleeves": "✅",
    "No sleeves found": "🟡",
    "API errors": "⚠️",
}


def _format_list(title: str, items: List[str], limit: int = 8) -> str:
    """Wypunktowanie ze skróceniem — pełna lista i tak nie zmieściłaby się
    w limicie Telegrama."""
    if not items:
        return ""

    displayed = items[:limit]
    remainder = len(items) - len(displayed)
    bullet = LIST_ICONS.get(title, "•")

    lines = "\n".join(f"{bullet} {item}" for item in displayed)
    if remainder > 0:
        lines += f"\n{bullet} i {remainder} więcej…"
    return f"\n*{title}*\n{lines}\n"


async def send_scrape_message(
    scraper_name: str,
    status: str,
    start_time: datetime,
    end_time: datetime,
    stats: Dict[str, int],
    lists: Dict[str, List[str]],
    notes: Optional[str] = None,
    *,
    severity: str = "success",
) -> None:
    # Sukcesy milczą, chyba że ktoś jawnie je włączy. „error" i „summary"
    # przechodzą zawsze.
    if severity == "success" and not NOTIFY_SUCCESS:
        return
    if not BOT_TOKEN or not CHAT_ID:
        return

    duration = end_time - start_time
    clean_duration = str(duration).split(".")[0]
    time_format = "%Y-%m-%d %H:%M:%S"
    lines: List[str] = []
    lines.append(f"🎯 *{scraper_name}* — {status}")
    lines.append("")
    lines.append(f"🟢 Start: *{start_time.strftime(time_format)}*")
    lines.append(f"🔴 End: *{end_time.strftime(time_format)}*")
    lines.append(f"⏱️ Duration: *{clean_duration}*")
    if notes:
        lines.append("")
        lines.append(f"💬 {notes}")
    # Nagłówek tylko wtedy, gdy jest co pod nim postawić. Wcześniej dopisywał
    # się zawsze i przy dobie bez zmian wiadomość kończyła się samym „Stats".
    stats_lines = [
        f"{STAT_ICONS.get(key, '•')} {key}: *{value}*" for key, value in stats.items()
    ]
    if stats_lines:
        lines.append("")
        lines.append("*Stats*")
        lines.extend(stats_lines)
        lines.append("")
    for title, items in lists.items():
        list_block = _format_list(title, items)
        if list_block:
            lines.append(list_block)

    await send_text_message("\n".join(filter(None, lines)))


async def send_text_message(text: str) -> None:
    """Jedyne miejsce, przez które idzie ruch do Telegrama.

    Dotąd każda wysyłka budowała własnego klienta bez timeoutu i połykała
    odpowiedź, więc odrzucona wiadomość (najczęściej zepsuty Markdown w tytule
    gry) ginęła bez śladu.
    """
    if not BOT_TOKEN or not CHAT_ID:
        return

    if len(text) > TELEGRAM_LIMIT:
        text = text[: TELEGRAM_LIMIT - 24].rstrip() + "\n\n… (ucięte)"

    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        log_error(f"⚠️ Telegram — wysyłka nieudana: {exc}")
        return

    if response.status_code >= 400:
        log_error(
            f"⚠️ Telegram odrzucił wiadomość ({response.status_code}): "
            f"{response.text[:200]}"
        )


async def send_job_failure(
    job: str,
    error: str,
    *,
    trigger: str,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
) -> None:
    """Powiadomienie o nieudanym zadaniu — jedno na każdą awarię, bez dławienia."""
    when = finished_at or datetime.utcnow()

    lines = [
        f"❌ *{SERVICE_NAME}* — zadanie `{job}` nie powiodło się",
        "",
        f"🕒 {when.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"🎬 Uruchomienie: *{trigger}*",
    ]
    if started_at and finished_at:
        lines.append(f"⏱️ Czas: *{str(finished_at - started_at).split('.')[0]}*")

    # Backticki z treści wyjątku rozwaliłyby blok kodu w Markdownie.
    safe_error = (error or "brak szczegółów").replace("`", "'")
    lines.extend(["", f"```\n{safe_error[:600]}\n```"])

    await send_text_message("\n".join(lines))
