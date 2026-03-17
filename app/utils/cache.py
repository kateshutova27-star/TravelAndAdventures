"""Redis cache utilities with stampede protection."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from redis.asyncio import Redis


def make_cache_key(prefix: str, **params: Any) -> str:
    """Build a deterministic cache key. Rounds lat/lng to 3 decimals (~111m)."""
    normalized: dict[str, Any] = {}
    for k, v in sorted(params.items()):
        if v is None:
            continue
        if k in ("lat", "lng") and isinstance(v, (int, float)):
            v = round(float(v), 3)
        normalized[k] = v
    raw = json.dumps(normalized, sort_keys=True, default=str)
    h = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"{prefix}:{h}"


async def cache_get(redis: Redis, key: str) -> Any | None:
    """Get a value from Redis cache, returns deserialized JSON or None."""
    data = await redis.get(key)
    if data is None:
        return None
    return json.loads(data)


async def cache_set(redis: Redis, key: str, value: Any, ttl: int = 1_209_600) -> None:
    """Set a value in Redis cache as JSON."""
    await redis.set(key, json.dumps(value, default=str), ex=ttl)


async def cached_query(
    redis: Redis,
    key: str,
    ttl: int,
    fetch_fn: Callable[[], Any],
) -> Any:
    """Execute fetch_fn with cache-aside + stampede protection via SETNX lock."""
    cached = await cache_get(redis, key)
    if cached is not None:
        return cached

    lock_key = f"{key}:lock"
    acquired = await redis.set(lock_key, "1", ex=ttl, nx=True)

    result = await fetch_fn()

    if acquired:
        await cache_set(redis, key, result, ttl)
        await redis.delete(lock_key)

    return result
