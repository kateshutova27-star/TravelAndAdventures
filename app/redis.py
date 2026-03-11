from redis.asyncio import ConnectionPool, Redis

from app.config import get_settings

_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    """Return a lazily-initialized Redis connection pool (singleton)."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
    return _pool


async def get_redis() -> Redis:
    """Return an async Redis client backed by the shared connection pool."""
    return Redis(connection_pool=_get_pool())


async def close_redis() -> None:
    """Gracefully shut down the Redis connection pool."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
