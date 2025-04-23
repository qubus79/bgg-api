# README.md

## FastAPI Backend — BGG Collection

Backend do pobierania kolekcji gier z BoardGameGeek i zapisywania ich do bazy PostgreSQL.

---

## 🔧 Lokalne uruchomienie (Docker)

### 1. Skonfiguruj `.env`

Utwórz plik `.env` na bazie `.env.example` i podaj dane do lokalnej bazy:

```
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/bgg
```

### 2. Uruchom Dockera

```
docker-compose up --build
```

### 3. API będzie dostępne pod:

- http://localhost:8000/health
- http://localhost:8000/stats
- http://localhost:8000/bgg_collection
- POST http://localhost:8000/update_bgg_collection

---

## ☁️ Deployment na Railway

### 1. Utwórz projekt Railway

- Wejdź na [https://railway.app](https://railway.app) i utwórz nowy projekt
- Wybierz opcję "Deploy from GitHub repo"

### 2. Dodaj zmienne środowiskowe

W zakładce **Variables**:

```
DATABASE_URL=postgresql+asyncpg://user:password@your-db-host:5432/bgg
```

Railway automatycznie przypisze host, port i hasło do bazy PostgreSQL jeśli dodasz usługę `PostgreSQL`.

### 3. Zmiana portu

Railway używa portu środowiskowego, upewnij się, że w `main.py` masz:

```python
import os
port = int(os.getenv("PORT", 8000))
uvicorn.run(app, host="0.0.0.0", port=port)
```

---

## 🌐 Endpointy

| Endpoint | Metoda | Opis |
|----------|--------|------|
|`/health`|GET|Sprawdzenie czy API działa|
|`/stats`|GET|Liczba gier w kolekcji + ostatni update|
|`/bgg_collection`|GET|Lista gier z kolekcji BGG z bazy|
|`/update_bgg_collection`|POST|Ręczne odpalenie scrapera i update bazy|

---

## 🐘 docker-compose.yml (dla dev)

```yaml
version: '3.9'

services:
  db:
    image: postgres:15
    restart: always
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: bgg
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  api:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    volumes:
      - .:/app
    ports:
      - "8000:8000"
    depends_on:
      - db

volumes:
  postgres_data:
```