import os
import logging
from typing import Any, Dict, Optional

import httpx

from app.services.bgg.session_store import build_session_store


logger = logging.getLogger(__name__)


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
        logger.info("BGG session cache: INVALIDATE")

    async def ensure_session(self, client: httpx.AsyncClient) -> None:
        """
        Ensure the provided httpx client has valid cookies in its cookie jar.
        """
        store = await self._get_store()
        cached = await store.get()
        if cached:
            logger.info("BGG session cache: HIT")
            # Load cookies into client's jar
            for k, v in cached.items():
                if v is not None:
                    client.cookies.set(k, v)
            return

        logger.info("BGG session cache: MISS")
        logger.info("BGG login: starting")
        await self._login_and_cache(client)
        logger.info("BGG login: success")

    async def _login_and_cache(self, client: httpx.AsyncClient) -> None:
        if not self._username or not self._password:
            raise RuntimeError("BGG_USERNAME / BGG_PASSWORD are required to fetch private collection data.")

        payload = {"credentials": {"username": self._username, "password": self._password}}
        headers = {"content-type": "application/json"}

        resp = await client.post(self._login_url, json=payload, headers=headers)

        # BGG login API commonly returns 204 No Content on success (while setting cookies)
        if resp.status_code not in (200, 204):
            # Keep a short body preview (may be empty)
            body_preview = (resp.text or "")[:200]
            raise RuntimeError(f"BGG login failed: HTTP {resp.status_code} ({body_preview})")

        # Extract cookies from the client's cookie jar (preferred)
        cookie_dict: Dict[str, Any] = {}
        for c in client.cookies.jar:
            cookie_dict[c.name] = c.value

        # If jar is missing SessionID, try to hydrate it from Set-Cookie headers
        if "SessionID" not in cookie_dict:
            set_cookie = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else resp.headers.get("set-cookie")
            # `set_cookie` can be a list or a single string depending on httpx version
            if set_cookie:
                if isinstance(set_cookie, str):
                    set_cookie = [set_cookie]
                try:
                    # Manually set cookies into the jar by parsing common cookie pairs
                    for sc in set_cookie:
                        # We only need the first "name=value" part
                        pair = sc.split(";", 1)[0].strip()
                        if "=" in pair:
                            name, value = pair.split("=", 1)
                            client.cookies.set(name.strip(), value.strip())
                    # Rebuild dict from jar
                    cookie_dict = {}
                    for c in client.cookies.jar:
                        cookie_dict[c.name] = c.value
                except Exception:
                    pass

        # Minimal sanity: SessionID is the key cookie for private endpoints
        if "SessionID" not in cookie_dict:
            raise RuntimeError("BGG login succeeded but SessionID cookie missing.")

        store = await self._get_store()
        await store.set(cookie_dict, ttl_seconds=self._ttl_seconds)