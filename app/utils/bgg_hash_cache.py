import hashlib
import importlib
import json
import logging
import os
from datetime import datetime
from typing import Any, Optional


logger = logging.getLogger(__name__)


class BGGHashCache:
    def __init__(self, redis_client: Any, prefix: str) -> None:
        self._redis = redis_client
        self._prefix = prefix.rstrip(":")

    def _key(self, suffix: str, identifier: int) -> str:
        return f"{self._prefix}:{suffix}:{identifier}"

    async def get_hash(self, suffix: str, identifier: int) -> Optional[str]:
        return await self._redis.get(self._key(suffix, identifier))

    async def set_hash(self, suffix: str, identifier: int, value: str) -> None:
        key = self._key(suffix, identifier)
        await self._redis.set(key, value)
        logger.debug("Hash cache set %s=%s", key, value[:8])

    async def delete_hash(self, suffix: str, identifier: int) -> None:
        await self._redis.delete(self._key(suffix, identifier))

    async def get_collection_hash(self, bgg_id: int) -> Optional[str]:
        return await self.get_hash("collection", bgg_id)

    async def set_collection_hash(self, bgg_id: int, value: str) -> None:
        await self.set_hash("collection", bgg_id, value)

    async def get_detail_hash(self, bgg_id: int) -> Optional[str]:
        return await self.get_hash("detail", bgg_id)

    async def set_detail_hash(self, bgg_id: int, value: str) -> None:
        await self.set_hash("detail", bgg_id, value)


def _normalize_for_hash(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _normalize_for_hash(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_for_hash(v) for v in value]
    return value


def compute_payload_hash(payload: Any) -> str:
    normalized = _normalize_for_hash(payload)
    text = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def build_hash_cache() -> Optional[BGGHashCache]:
    redis_url = os.getenv("BGG_HASH_REDIS_URL")
    if not redis_url:
        logger.info("BGG hash cache disabled (BGG_HASH_REDIS_URL not set)")
        return None

    try:
        redis_module = importlib.import_module("redis.asyncio")
        password = os.getenv("BGG_HASH_REDIS_PASSWORD")
        db = int(os.getenv("BGG_HASH_REDIS_DB", "0"))
        prefix = os.getenv("BGG_HASH_REDIS_PREFIX", "bgg_game_hash")
        client = redis_module.from_url(redis_url, password=password, db=db, decode_responses=True)
        await client.ping()
        logger.info("BGG hash cache connected to Redis %s db=%s prefix=%s", redis_url, db, prefix)
        return BGGHashCache(client, prefix)
    except Exception as exc:
        logger.warning("BGG hash cache unavailable (%s): %s", redis_url, exc)
        return None
