import re
from typing import AsyncGenerator

from fastapi import Header, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory

_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis


async def get_device_id(
    x_device_id: str | None = Header(default=None),
) -> str:
    if not x_device_id or not _UUID_V4_RE.match(x_device_id):
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid X-Device-Id header. Must be a valid UUID v4.",
        )
    return x_device_id
