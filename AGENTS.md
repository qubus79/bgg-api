# AGENTS.md
# Guide for agentic coding assistants in this repo

This repository is a FastAPI backend for BoardGameGeek collection/hotness data.
Use this guide to follow existing conventions and avoid guessing about tooling.

----------------------------------------
Project overview
----------------------------------------
- App entrypoint: app/main.py
- Async stack: FastAPI + SQLAlchemy async + httpx
- Schedulers: APScheduler tasks in app/tasks
- Scrapers: app/scraper (BGG XML/JSON)
- DB: PostgreSQL (SQLAlchemy models in app/models)
- Deploy: Railway (app + Postgres are on Railway)

----------------------------------------
Build, run, lint, test
----------------------------------------
There is no dedicated build step; this is a Python service.

Local run (dev):
- uvicorn app.main:app --reload

Install dependencies:
- python -m venv venv
- source venv/bin/activate
- pip install -r requirements.txt

Linting:
- No linter configured in repo (no ruff/flake8/black config found).
- Do not invent lint commands. If you add a linter, document it here.

Testing:
- No tests or test config found (no tests/ directory, pytest.ini, tox.ini, etc.).
- Do not invent test commands.

Single test execution (only if tests are added later):
- pytest tests/test_file.py::test_name
- pytest -k "test_name"
Note: These are standard pytest patterns and are not currently configured.

----------------------------------------
Environment and deployment
----------------------------------------
Deployment is on Railway. The PostgreSQL database is also hosted on Railway.
Expect production config to be set via Railway environment variables.

Key environment variables used in code:
- DATABASE_URL (SQLAlchemy async connection string)
- BGG_USERNAME / BGG_PASSWORD (private collection access)
- BGG_API_TOKEN (optional for BGG XML API)
- BGG_PRIVATE_USER_ID
- BGG_SESSION_CACHE_TTL_SECONDS
- BGG_LOGIN_URL
- REDIS_URL (optional, enables Redis-backed session cache)
- BGG_HASH_REDIS_URL / BGG_HASH_REDIS_PASSWORD / BGG_HASH_REDIS_DB (dedicated Redis instance for collection/detail hashes; keeps the hash cache separate from `session_store`)
- USER_AGENT (plays scraper)
- BGG_PLAYS_DELAY_SECONDS
- BGG_PLAYS_SHOWCOUNT
- BGG_PLAYS_SYNC_HOURS
- HTTP2 (hotness/accessory scrapers)

----------------------------------------
Code style and conventions
----------------------------------------
Imports and structure:
- Group imports as: standard library, third-party, then app-local.
- Prefer explicit imports over wildcard imports.
- Keep router modules thin; move heavy logic to app/tasks or app/scraper.

Formatting:
- The repo does not enforce an auto-formatter. Keep formatting consistent
  with existing files (PEP 8-ish, 4 spaces, readable line lengths).
- Use blank lines to separate logical blocks and top-level sections.

Typing and annotations:
- Use type hints where practical; most code uses Optional, Dict, Any.
- Prefer `Optional[T]` or `T | None` in new code, matching nearby style.
- Use precise types for function signatures in services/scrapers.

Naming:
- snake_case for functions, variables, and module names.
- PascalCase for classes (SQLAlchemy models, helpers).
- UPPER_SNAKE_CASE for constants (URLs, defaults).

Async patterns:
- Use async functions end-to-end (FastAPI, SQLAlchemy async sessions).
- Use `AsyncSessionLocal()` context managers for DB access.
- Ensure commits are explicit after DB writes.

Database access:
- Models live in app/models; keep schema changes consistent with models.
- When updating many fields, iterate dict and setattr (as done in scrapers).
- Avoid blocking I/O in request handlers.

HTTP and scraping:
- Use httpx.AsyncClient with explicit headers and timeouts.
- Handle BGG rate limiting with retries/backoff (see app/scraper/bgg_game.py).
- Use authenticated session manager for private endpoints
  (app/services/bgg/auth_session.py).
- Persist collection/detail hashes in Redis (see app/utils/bgg_hash_cache.py) so that `bgg_game` only fetches `/thing` when either collection metadata or detail payload changed. The hash uses the dedicated `BGG_HASH_REDIS_*` instance, not the session store.

Error handling:
- Use explicit RuntimeError for hard failures (e.g., missing credentials).
- For external HTTP, check status codes and add backoff or retries.
- Log errors via app.utils.logging helpers or standard logging.

Logging:
- Prefer app.utils.logging.log_info/log_success/log_warning/log_error
  for user-visible console logs.
- For library-like modules, use logging.getLogger(__name__).

Schedulers:
- Schedulers are initialized on startup in app/main.py.
- Intervals are configured in app/tasks and often rely on env vars.

Data integrity:
- Be careful with migration-like changes; this repo does not include
  Alembic or migration tooling. Avoid adding fields without a plan.

----------------------------------------
Repository map (key paths)
----------------------------------------
- app/main.py: FastAPI app + startup + routers
- app/database.py: SQLAlchemy async engine/session
- app/models/: SQLAlchemy models
- app/routes/: API routes (thin)
- app/tasks/: scheduler jobs and DB aggregation
- app/scraper/: BGG fetch and parse logic
- app/services/: supporting services (auth/session cache)
- app/utils/logging.py: console logging helpers

----------------------------------------
Cursor/Copilot rules
----------------------------------------
No Cursor rules or Copilot instructions were found in this repo.

----------------------------------------
Notes for agents
----------------------------------------
- Do not add new tooling without updating this file.
- Keep changes focused; avoid refactors unless required.
- Respect Railway deployment assumptions (env vars, Postgres on Railway).
