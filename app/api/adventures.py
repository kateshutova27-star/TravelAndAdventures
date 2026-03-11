"""Adventures API endpoints."""

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_device_id, get_redis
from app.schemas.adventure import (
    AdventureResponse,
    AdventureStatusResponse,
    AdventureStopResponse,
    AdventureUpdateRequest,
    GenerateAdventureRequest,
    GenerateAdventureResponse,
)
from app.schemas.common import GenerationStatus, PlaceCategory
from app.schemas.place import PlaceResponse
from app.services import adventure_service

router = APIRouter()


def _adventure_to_response(adventure) -> AdventureResponse:
    stops = []
    for stop in (adventure.stops or []):
        place = stop.place
        stops.append(AdventureStopResponse(
            place=PlaceResponse(
                id=str(place.id),
                name=place.name,
                category=PlaceCategory(place.category),
                lat=place.lat,
                lng=place.lng,
                thumbnail_url=place.thumbnail_url,
                photo_urls=place.photo_urls or [],
                rating=place.rating or 0.0,
                review_count=place.review_count or 0,
                description=place.description or "",
                tags=place.tags or [],
                best_time_to_visit=place.best_time_to_visit,
                working_hours=place.working_hours,
            ),
            drive_time_minutes=stop.drive_time_minutes,
            time_to_spend_minutes=stop.time_to_spend_minutes,
            order=stop.order,
        ))
    return AdventureResponse(
        id=str(adventure.id),
        stops=stops,
        total_time_minutes=adventure.total_time_minutes,
        drive_time_minutes=adventure.drive_time_minutes,
        encoded_polyline=adventure.encoded_polyline,
        created_at=adventure.created_at,
    )


# IMPORTANT: POST /generate and GET / registered BEFORE GET /{adventure_id}


@router.post("/generate", response_model=GenerateAdventureResponse, status_code=202)
async def generate_adventure(
    body: GenerateAdventureRequest,
    device_id: str = Depends(get_device_id),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> GenerateAdventureResponse:
    """Start adventure generation. Returns a task_id to poll for status."""
    params = {
        "lat": body.lat,
        "lng": body.lng,
        "duration": body.duration.value,
        "categories": [c.value for c in body.categories],
        "place_ids": body.place_ids,
    }

    adventure = await adventure_service.create_adventure(db, device_id, params)
    task_id = str(adventure.id)

    # Set initial status in Redis for fast polling
    await redis.hset(f"adventure:{task_id}:progress", mapping={"status": "pending"})

    # Enqueue background task via ARQ
    arq_redis = ArqRedis(pool_or_conn=redis.connection_pool)
    await arq_redis.enqueue_job("generate_adventure_task", task_id, params)

    return GenerateAdventureResponse(task_id=task_id)


@router.get("", response_model=list[AdventureResponse])
async def list_adventures(
    saved: bool = True,
    device_id: str = Depends(get_device_id),
    db: AsyncSession = Depends(get_db),
) -> list[AdventureResponse]:
    """List saved adventures for a device."""
    adventures = await adventure_service.get_saved_adventures(db, device_id)
    return [_adventure_to_response(a) for a in adventures]


@router.get("/{adventure_id}/status", response_model=AdventureStatusResponse)
async def get_adventure_status(
    adventure_id: str,
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
) -> AdventureStatusResponse:
    """Get the generation status of an adventure."""
    # Try Redis first (fast path)
    progress = await redis.hgetall(f"adventure:{adventure_id}:progress")
    if progress:
        status_str = progress.get("status", "pending")
        adv_id = progress.get("adventure_id")
        try:
            status = GenerationStatus(status_str)
        except ValueError:
            status = GenerationStatus.pending
        return AdventureStatusResponse(status=status, adventure_id=adv_id)

    # Fallback to DB
    adventure = await adventure_service.get_adventure(db, adventure_id)
    if not adventure:
        raise HTTPException(status_code=404, detail="Adventure not found")

    try:
        status = GenerationStatus(adventure.status)
    except ValueError:
        status = GenerationStatus.pending

    adv_id = str(adventure.id) if adventure.status == "completed" else None
    return AdventureStatusResponse(status=status, adventure_id=adv_id)


@router.get("/{adventure_id}", response_model=AdventureResponse)
async def get_adventure(
    adventure_id: str,
    db: AsyncSession = Depends(get_db),
) -> AdventureResponse:
    """Get adventure details with stops and places."""
    adventure = await adventure_service.get_adventure(db, adventure_id)
    if not adventure:
        raise HTTPException(status_code=404, detail="Adventure not found")
    return _adventure_to_response(adventure)


@router.patch("/{adventure_id}", response_model=AdventureResponse)
async def update_adventure(
    adventure_id: str,
    body: AdventureUpdateRequest,
    device_id: str = Depends(get_device_id),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> AdventureResponse:
    """Update an adventure (reorder/edit stops). Rebuilds route."""
    # Verify ownership
    adventure = await adventure_service.get_adventure(db, adventure_id)
    if not adventure:
        raise HTTPException(status_code=404, detail="Adventure not found")
    if str(adventure.created_by_device_id) != device_id:
        raise HTTPException(status_code=403, detail="Not authorized to edit this adventure")

    # Build stops data from request
    stops_data = [
        {
            "place_id": str(stop.place.id),
            "order": stop.order,
            "drive_time_minutes": stop.drive_time_minutes,
            "time_to_spend_minutes": stop.time_to_spend_minutes,
        }
        for stop in body.stops
    ]

    # TODO: rebuild route via Geoapify for new stop order
    # For now, save the new stop configuration as-is
    total_time = sum(s.drive_time_minutes + s.time_to_spend_minutes for s in body.stops)
    drive_time = sum(s.drive_time_minutes for s in body.stops)

    updated = await adventure_service.update_adventure_stops(
        db, adventure_id, stops_data, total_time, drive_time,
        adventure.encoded_polyline or "",
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Adventure not found")

    # Invalidate cache
    await redis.delete(f"adventure:{adventure_id}")

    return _adventure_to_response(updated)


@router.post("/{adventure_id}/save", status_code=204)
async def save_adventure(
    adventure_id: str,
    device_id: str = Depends(get_device_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Save an adventure for a device."""
    await adventure_service.save_adventure(db, device_id, adventure_id)


@router.delete("/{adventure_id}/save", status_code=204)
async def unsave_adventure(
    adventure_id: str,
    device_id: str = Depends(get_device_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a saved adventure for a device."""
    await adventure_service.unsave_adventure(db, device_id, adventure_id)
