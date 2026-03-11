"""ARQ worker for background adventure generation."""

from __future__ import annotations

import logging

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.database import async_session_factory
from app.services.adventure_generator import generate_adventure
from app.services.adventure_service import set_adventure_result, update_adventure_status

logger = logging.getLogger(__name__)


async def generate_adventure_task(ctx: dict, adventure_id: str, params: dict) -> None:
    """Background task: generate an adventure and save the result."""
    redis = ctx.get("redis")

    async def update_status(status: str) -> None:
        if redis:
            await redis.hset(f"adventure:{adventure_id}:progress", mapping={"status": status})
        async with async_session_factory() as db:
            await update_adventure_status(db, adventure_id, status)

    try:
        await update_status("findingPlaces")

        async with async_session_factory() as db:
            result = await generate_adventure(
                db=db,
                lat=params["lat"],
                lng=params["lng"],
                duration=params["duration"],
                categories=params.get("categories", []),
                update_status_fn=update_status,
            )

        async with async_session_factory() as db:
            await set_adventure_result(
                db=db,
                adventure_id=adventure_id,
                stops_data=result["stops"],
                total_time_minutes=result["total_time_minutes"],
                drive_time_minutes=result["drive_time_minutes"],
                encoded_polyline=result["encoded_polyline"],
            )

        if redis:
            await redis.hset(
                f"adventure:{adventure_id}:progress",
                mapping={"status": "completed", "adventure_id": adventure_id},
            )

        logger.info("Adventure %s generated successfully", adventure_id)

    except Exception:
        logger.exception("Adventure generation failed: %s", adventure_id)
        await update_status("failed")
        async with async_session_factory() as db:
            await update_adventure_status(
                db, adventure_id, "failed", error_message="Generation failed. Please try again."
            )
        if redis:
            await redis.hset(
                f"adventure:{adventure_id}:progress",
                mapping={"status": "failed"},
            )


async def startup(ctx: dict) -> None:
    """ARQ worker startup."""
    logger.info("ARQ worker started")


async def shutdown(ctx: dict) -> None:
    """ARQ worker shutdown."""
    logger.info("ARQ worker shutting down")


class WorkerSettings:
    """ARQ worker configuration."""

    functions = [generate_adventure_task]
    on_startup = startup
    on_shutdown = shutdown

    _settings = get_settings()
    redis_settings = RedisSettings.from_dsn(_settings.REDIS_URL)
    max_jobs = _settings.ARQ_MAX_JOBS
    job_timeout = _settings.ARQ_JOB_TIMEOUT
    max_tries = 2
    retry_delay = 5
