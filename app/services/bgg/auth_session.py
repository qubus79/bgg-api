import os
from typing import Any, Dict, Optional

import httpx

from app.services.bgg.session_store import build_session_store


class BGGAuthSessionManager:
    """
    Maintains a logged-in BGG session (cookies) for private endpoints.

    Strategy:
    - Cookies are cached (Redis if REDIS_URL, else in-memory).
    - ensure_session() logs in if cache is empty/expired.
    - invalidate() clears cache, forcing re-login.
    """

    def __init__(self) -> None:
        self._store = None
        self._ttl_seconds = int(os.getenv("BGG_SESSION_CACHE_TTL_SECONDS", "28800"))  # default 8h

        self._username = os.getenv("BGG_USERNAME")
        self._password = os.getenv("BGG_PASSWORD")

        self._login_url = os.getenv("BGG_LOGIN_URL", "https://boardgamegeek.com/login/api/v1")

    async def _get_store(self):
        if self._store is None:
            self._store = await build_session_store()
        return self._store

    async def invalidate(self) -> None:
        store = await self._get_store()
        await store.delete()

    async def ensure_session(self, client: httpx.AsyncClient) -> None:
        """
        Ensure the provided httpx client has valid cookies in its cookie jar.
        """
        store = await self._get_store()
        cached = await store.get()
        if cached:
            # Load cookies into client's jar
            for k, v in cached.items():
                if v is not None:
                    client.cookies.set(k, v)
            return

        # No cached cookies => login
        await self._login_and_cache(client)

    async def _login_and_cache(self, client: httpx.AsyncClient) -> None:
        if not self._username or not self._password:
            raise RuntimeError("BGG_USERNAME / BGG_PASSWORD are required to fetch private collection data.")

        payload = {"credentials": {"username": self._username, "password": self._password}}
        headers = {"content-type": "application/json"}

        resp = await client.post(self._login_url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"BGG login failed: HTTP {resp.status_code} ({resp.text[:200]})")

        # Extract cookies from the client's cookie jar (preferred)
        cookie_dict: Dict[str, Any] = {}
        for c in client.cookies.jar:
            cookie_dict[c.name] = c.value

        # Minimal sanity: SessionID is the key cookie for private endpoints
        if "SessionID" not in cookie_dict:
            # Sometimes httpx jar needs a redirect follow; but you already have follow_redirects=True
            raise RuntimeError("BGG login succeeded but SessionID cookie missing.")

        store = await self._get_store()
        await store.set(cookie_dict, ttl_seconds=self._ttl_seconds)