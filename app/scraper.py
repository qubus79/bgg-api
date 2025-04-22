# app/scraper.py

import httpx
from bs4 import BeautifulSoup
from app.config import settings
from collections import Counter

async def fetch_html(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


async def fetch_additional_info(client, game_url: str) -> dict:
    html = await fetch_html(client, game_url)
    soup = BeautifulSoup(html, 'html.parser')

    info = {
        "original_title": None,
        "author": None,
        "players": None,
        "age": None,
        "play_time": None,
        "publisher": None,
        "description": None,
        "bgg_link": None,
        "main_image": None,
        "release_info": None,
        "info_tags": None,
    }

    cover = soup.select_one('div.col-md-4.bg-light img.img-fluid')
    if cover and cover.get('src'):
        info["main_image"] = cover['src']

    details = soup.select('.main-description dl')
    if details:
        for dt, dd in zip(details[0].find_all('dt'), details[0].find_all('dd')):
            label = dt.text.strip().lower()
            value = dd.text.strip()

            if "tytuł oryginalny" in label:
                info["original_title"] = value
            elif "autor" in label:
                info["author"] = value
            elif "szczegóły" in label:
                players_span = dd.find('span', class_='text-danger')
                age_span = dd.find('span', class_='text-primary')
                time_span = dd.find('span', class_='text-success')
                if players_span:
                    info["players"] = players_span.text.strip()
                if age_span:
                    info["age"] = age_span.text.strip()
                if time_span:
                    info["play_time"] = time_span.text.strip()
            elif "wydawnictwo" in label:
                info["publisher"] = value
            elif "linki" in label:
                for a in dd.find_all('a', href=True):
                    if "boardgamegeek.com" in a['href']:
                        info["bgg_link"] = a['href']
            elif "premiera" in label:
                info["release_info"] = value
            elif "informacje" in label:
                info["info_tags"] = value

    desc_block = soup.select_one('.main-description .fst-italic p')
    if desc_block:
        info["description"] = desc_block.get_text(separator="\n", strip=True)

    return info


async def fetch_all_premieres_raw() -> list:
    async with httpx.AsyncClient() as client:
        html = await fetch_html(client, settings.ZNADPLANSZY_URL)
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find_all('table')[0]
        rows = table.find_all('tr')

        raw_game_data = []

        for row in rows:
            cells = row.find_all('td')
            if not cells:
                continue

            image_url = cells[0].find('img')['src']
            game_url_suffix = cells[0].find('a')['href']
            game_url = f"https://premiery.znadplanszy.pl{game_url_suffix}"

            # # Extract game_id from URL, e.g., /game/12345 -> 12345
            # game_id = None
            # if game_url_suffix.startswith("/game/"):
            #     game_id = game_url_suffix.strip("/").split("/")[-1]

            # Extract game_id from URL, e.g., /game/12345 -> 12345 (as int)
            game_id = None
            if game_url_suffix.startswith("/game/"):
                try:
                    game_id = int(game_url_suffix.strip("/").split("/")[-1])
                except ValueError:
                    print(f"⚠️ Nieprawidłowe game_id w URL: {game_url}")
                    game_id = None

            title = cells[1].text.splitlines()
            game_name = title[2]
            designers = cells[2].text.strip()
            status = cells[3].text.strip()
            release_date = f'{cells[4].text.strip()} {cells[5].text.strip()}'
            release_year = cells[5].text.strip()
            release_period = cells[4].text.strip()
            publisher = cells[6].text.strip()
            additional_info = cells[7].text.strip()
            game_type = cells[8].text.strip()

            additional_details = await fetch_additional_info(client, game_url)

            raw_game_data.append({
                "game_id": game_id,
                "game_name": game_name,
                "designers": designers,
                "status": status,
                "release_date": release_date,
                "release_period": release_period,
                "release_year": release_year,
                "publisher": publisher,
                "game_type": game_type,
                "additional_info": additional_info,
                "game_image": image_url,
                "game_url": game_url,
                "additional_details": additional_details
            })

        # 🔍 Logika wykrywania i usuwania duplikatów
        ids = [g["game_id"] for g in raw_game_data if g["game_id"]]
        dupes = {k: v for k, v in Counter(ids).items() if v > 1}
        if dupes:
            print(f"🧨 Duplikaty wykryte: {dupes}")

        seen = set()
        unique_game_data = []
        for g in raw_game_data:
            gid = g["game_id"]
            if gid and gid not in seen:
                seen.add(gid)
                unique_game_data.append(g)

        return unique_game_data
