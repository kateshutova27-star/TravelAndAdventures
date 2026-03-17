"""ARQ worker for background adventure generation."""

from __future__ import annotations

import logging

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.database import async_session_factory
from app.services.adventure_generator import generate_adventure
from app.services.adventure_service import set_adventure_result, update_adventure_status
from app.services.routing_service import RoutingServiceError

logger = logging.getLogger(__name__)


async def generate_adventure_task(ctx: dict, adventure_id: str, params: dict) -> None:
    """Background task: generate an adventure and save the result."""
    redis = ctx.get("redis")

    async def update_status(status: str) -> None:
        if redis:
            await redis.hset(f"adventure:{adventure_id}:progress", mapping={"status": status})
        async with async_session_factory() as db:
            await update_adventure_status(db, adventure_id, status)

    async def _mark_failed(adv_id: str, r, error_msg: str) -> None:
        await update_status("failed")
        async with async_session_factory() as db:
            await update_adventure_status(db, adv_id, "failed", error_message=error_msg)
        if r:
            await r.hset(
                f"adventure:{adv_id}:progress",
                mapping={"status": "failed", "error": error_msg},
            )

    try:
        await update_status("findingPlaces")

        async with async_session_factory() as db:
            result = await generate_adventure(
                db=db,
                lat=params["lat"],
                lng=params["lng"],
                duration=params["duration"],
                categories=params.get("categories", []),
                place_ids=params.get("place_ids", []),
                radius_km=params.get("radius_km"),
                excluded_place_ids=params.get("excluded_place_ids", []),
                boosted_place_ids=params.get("boosted_place_ids", []),
                redis=redis,
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

    except RoutingServiceError as e:
        logger.exception("Routing API failed for adventure %s: %s", adventure_id, e)
        await _mark_failed(adventure_id, redis, "Could not calculate walking route. Please try again.")
    except ValueError as e:
        logger.warning("Adventure generation ValueError for %s: %s", adventure_id, e)
        await _mark_failed(adventure_id, redis, str(e) or "Generation failed. Please try again.")
    except Exception:
        logger.exception("Adventure generation failed: %s", adventure_id)
        await _mark_failed(adventure_id, redis, "Generation failed. Please try again.")
    except BaseException:
        # Catches CancelledError from ARQ job timeout — without this,
        # status stays stuck at "buildingRoute" forever
        logger.error("Adventure generation cancelled (timeout?): %s", adventure_id)
        error_msg = "Generation took too long. Please try again."
        try:
            await _mark_failed(adventure_id, redis, error_msg)
        except Exception:
            logger.warning("Failed to mark adventure %s as failed after cancellation", adventure_id)
        raise


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
    max_tries = 1
    retry_delay = 5
