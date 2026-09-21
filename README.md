# 🎲 BGG API – BoardGameGeek Collection & Hotness API

FastAPI backend do zbierania, przechowywania i udostępniania danych z BoardGameGeek, w tym:
- Głównej kolekcji użytkownika (gry)
- Akcesoriów z BGG
- Gier i osób z listy Hotness (najpopularniejszych w danym momencie)

Dane przechowywane są w bazie PostgreSQL i mogą być wykorzystywane przez aplikacje mobilne, webowe lub inne integracje (np. Notion).

---

## 📦 Funkcjonalności

- ✅ Import gier z kolekcji BGG (w tym statystyki, mechaniki, czas gry, itd.)
- ✅ Import akcesoriów z BGG
- ✅ Import „Hotness” – osobno dla gier i osób (autorzy, ilustratorzy itd.)
- ✅ Harmonogram (scheduler) aktualizacji danych (kolekcja co 3h, akcesoria co 6h, hotness co 6h, plays co 6h)
- ✅ REST API z punktami `/health`, `/stats`, `/update`, `/` dla każdego zasobu
- ✅ Gotowy do deploymentu na Railway, Render lub lokalnie via Docker

---

## 🗂 Struktura projektu

```
app/
├── main.py                     # FastAPI app + routowanie
├── database.py                # Konfiguracja bazy danych
├── models/                    # Modele SQLAlchemy (PostgreSQL)
│   ├── bgg_game.py
│   ├── bgg_accessory.py
│   ├── bgg_hotness_game.py
│   └── bgg_hotness_person.py
├── schemas/                   # Schematy Pydantic (API)
├── routes/                    # Endpointy API (FastAPI routers)
│   ├── bgg_game.py
│   ├── bgg_accessory.py
│   └── bgg_hotness.py         # (gra + osoba w jednym)
├── scraper/                   # Logika pobierania danych z BGG
│   ├── bgg_game.py
│   ├── bgg_accessory.py
│   └── bgg_hotness.py
├── tasks/                     # Harmonogramy aktualizacji danych
├── utils/                     # Logowanie, helpery
└── config.py                  # Ustawienia środowiska
```

---

## 🚀 Jak uruchomić lokalnie?

### 1. Wymagania
- Python 3.11+
- PostgreSQL
- (opcjonalnie) Docker + Docker Compose

### 2. Instalacja

```bash
# Klonuj repo
git clone https://github.com/twoj-uzytkownik/bgg-api.git
cd bgg-api

# Virtualenv
python -m venv venv
source venv/bin/activate

# Instalacja zależności
pip install -r requirements.txt
```

### 3. Skonfiguruj zmienne środowiskowe

Utwórz `.env` w katalogu głównym i dodaj np.:

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/games
BGG_USERNAME=qubus
```

### 4. Uruchom lokalnie

```bash
uvicorn app.main:app --reload
```

---

## 🌐 Deployment na Railway

1. Zaloguj się do Railway: https://railway.app/
2. Utwórz nowy projekt → Deploy from GitHub → wybierz repozytorium
3. Ustaw zmienne środowiskowe:
   - `DATABASE_URL`
   - `BGG_USERNAME`
4. Railway automatycznie zbuduje i uruchomi aplikację

---

## 🔗 Przykładowe endpointy

- `GET /bgg_games/health`
- `POST /bgg_games/update_bgg_collection`
- `GET /bgg_accessories/stats`
- `GET /bgg_hotness/games`
- `GET /bgg_hotness/people`

---

## 🧠 Autorzy i wsparcie

Projekt prywatny rozwijany przez [Paweł Nocznicki](mailto:pawel@nocznicki.pl) na potrzeby aplikacji do zarządzania kolekcją gier planszowych.

---

## 🛡 Licencja

MIT License – możesz używać, kopiować, modyfikować i wykorzystywać we własnych projektach.

## 🔔 Powiadomienia na Telegram

- Wiadomość przychodzi **tylko przy awarii** — jedna na każde nieudane zadanie,
  bez dławienia powtórek. Dotyczy tak samo harmonogramu, jak ręcznego uruchomienia
  z aplikacji.
- Raz na dobę, o **23:00 czasu polskiego**, idzie **osobne podsumowanie każdego syncu**:
  co się w ciągu dnia zmieniło (dodane / zaktualizowane / usunięte), ile było
  przebiegów i ile z nich padło. Liczby biorą się z tabeli `job_runs`.
- Zmienne: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_NOTIFY_SUCCESS`
  (domyślnie `false` — `true` przywraca wiadomość po każdym udanym przebiegu),
  `TELEGRAM_DAILY_SUMMARY` (domyślnie `true`), `TELEGRAM_SUMMARY_HOUR` (domyślnie `23`).
- Podsumowanie da się odpalić na żądanie: `POST /jobs/daily_summary/run`.
