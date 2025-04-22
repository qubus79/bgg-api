# README.md

## FastAPI Backend — ZnadPlanszy Premieres

### Jak odpalić lokalnie?

#### 1. Utwórz plik `.env` na bazie `.env.example`

```
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/games
ZNADPLANSZY_URL=https://premiery.znadplanszy.pl/catalogue
```

#### 2. Uruchom Dockera (dev lokalny)

```
docker-compose up --build
```

#### 3. API dostępne pod:

- http://localhost:8000/health
- http://localhost:8000/stats
- http://localhost:8000/premieres
- http://localhost:8000/debug_raw
- POST http://localhost:8000/update_premieres


### Endpointy

| Endpoint | Metoda | Opis |
|----------|--------|------|
|/health|GET|Sprawdzenie czy API działa|
|/stats|GET|Liczba premier + ostatni update|
|/premieres|GET|Lista premier z bazy|
|/debug_raw|GET|Surowy wynik scrapera|
|/update_premieres|POST|Ręczne odpalenie scrapera i update bazy|


---

# docker-compose.yml
version: '3.9'

services:
  db:
    image: postgres:15
    restart: always
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: games
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


