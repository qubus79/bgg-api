import os
import json
import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class InMemorySessionStore:
    def __init__(self) -> None:
        self._value: Optional[Dict[str, Any]] = None
        self._expires_at: Optional[float] = None

    async def get(self) -> Optional[Dict[str, Any]]:
        if self._value is None:
            return None
        if self._expires_at is not None and time.time() >= self._expires_at:
            self._value = None
            self._expires_at = None
            return None
        return self._value

    async def set(self, value: Dict[str, Any], ttl_seconds: int) -> None:
        self._value = value
        self._expires_at = time.time() + ttl_seconds

    async def delete(self) -> None:
        self._value = None
        self._expires_at = None


class RedisSessionStore:
    def __init__(self, redis_client: Any, key: str) -> None:
        self._redis = redis_client
        self._key = key

    async def get(self) -> Optional[Dict[str, Any]]:
        raw = await self._redis.get(self._key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def set(self, value: Dict[str, Any], ttl_seconds: int) -> None:
        await self._redis.set(self._key, json.dumps(value), ex=ttl_seconds)

    async def delete(self) -> None:
        await self._redis.delete(self._key)


async def build_session_store():
    """
    If REDIS_URL is set and redis is available, use Redis.
    Otherwise, fall back to in-memory.

    Logs which backend is used and why.
    """
    redis_url = os.getenv("REDIS_URL")

    if redis_url:
        try:
            import redis.asyncio as redis  # requires redis>=4

            client = redis.from_url(redis_url, decode_responses=True)

            # Try a cheap connectivity check so we can explain fallback clearly
            try:
                await client.ping()
            except Exception as e:
                logger.warning(
                    "Redis configured (REDIS_URL set) but ping failed; falling back to in-memory (%s)",
                    e.__class__.__name__,
                )
                return InMemorySessionStore()

            logger.info("Session store backend: Redis")
            return RedisSessionStore(client, key="bgg:session:cookies")

        except Exception as e:
            logger.warning(
                "Redis configured (REDIS_URL set) but redis client unavailable; falling back to in-memory (%s)",
                e.__class__.__name__,
            )
            return InMemorySessionStore()

    logger.info("Session store backend: In-memory (REDIS_URL not set)")
    return InMemorySessionStore()