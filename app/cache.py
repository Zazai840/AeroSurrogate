"""Redis cache layer.

Design points:

* We use redis.asyncio (built into redis-py >= 4.2). Do not install the
  separate `aioredis` package — it is deprecated and merged into redis-py.
* Cache key = SHA256(JSON(rounded inputs, sorted keys)). Rounding is
  essential. Two requests with the same intent will have input floats
  that differ at the 15th decimal due to float parsing; without rounding
  your cache hit rate is effectively zero.
* TTL-based expiry (cache-aside pattern). On a hit we return the cached
  value; on a miss we call the model, write the result to Redis, and
  return. This is the simplest correct strategy. Write-through and
  write-around are alternatives worth knowing but not worth implementing
  for this use case.
* RedisCache is instantiated once in the FastAPI lifespan and stored on
  app.state. There is no per-request connection — redis-py's client holds
  a connection pool internally.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import redis.asyncio as redis

from app.config import get_settings

settings = get_settings()

CACHE_KEY_PREFIX = "aero:pred:"
ROUNDING_DECIMALS = 6


def make_cache_key(inputs: dict[str, float]) -> str:
    """Deterministic cache key from input geometry.

    Rounds every float to 6 decimals then SHA256-hashes the canonical JSON
    representation. Same inputs in any order produce the same key.
    """
    rounded = {k: round(float(v), ROUNDING_DECIMALS) for k, v in inputs.items()}
    canonical = json.dumps(rounded, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}{digest}"


class RedisCache:
    """Async Redis cache wrapping a redis-py connection pool.

    Instantiate via the async factory `RedisCache.create()` which verifies
    connectivity before returning. Methods map directly onto cache-aside
    semantics: `get` returns None on a miss, `set` writes with TTL.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    async def create(cls, url: str) -> RedisCache:
        """Create a client, verify connectivity with PING, and return the cache.

        Called once in the FastAPI lifespan — not on every request.
        """
        client = redis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
        await client.ping()
        return cls(client)

    async def get(self, key: str) -> dict[str, Any] | None:
        """Return the cached value for key, or None on a miss."""
        raw = await self._client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set(
        self,
        key: str,
        value: dict[str, Any],
        ttl: int | None = None,
    ) -> None:
        """Store value under key with a TTL.

        Defaults to settings.cache_ttl_seconds when ttl is not supplied.
        """
        await self._client.set(
            key,
            json.dumps(value),
            ex=ttl if ttl is not None else settings.cache_ttl_seconds,
        )

    async def close(self) -> None:
        """Close the underlying connection pool gracefully."""
        await self._client.aclose()
