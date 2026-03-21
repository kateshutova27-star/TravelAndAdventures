"""Places API endpoints."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dependencies import get_db, get_device_id, get_redis
from app.schemas.common import PlaceCategory
from app.schemas.place import PlaceResponse
from app.services import place_service
from app.services.poi_loader import ensure_places_loaded, is_area_loaded
from app.utils.cache import cache_get, cache_set, make_cache_key

logger = logging.getLogger(__name__)

router = APIRouter()


def _place_to_response(place) -> PlaceResponse:
    return PlaceResponse(
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
    )


def _response_to_dict(resp: PlaceResponse) -> dict:
    return resp.model_dump(by_alias=True)


def _dict_to_response(d: dict) -> PlaceResponse:
    return PlaceResponse.model_validate(d)


@router.get("", response_model=list[PlaceResponse])
async def list_places(
    background_tasks: BackgroundTasks,
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    radius: float = Query(gt=0, le=50000, default=10000),
    category: PlaceCategory | None = Query(default=None),
    q: str | None = Query(default=None, min_length=2, max_length=200),
    limit: int = Query(default=200, ge=1, le=1500),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> list[PlaceResponse]:
    """List places near a location, optionally filtered by category or text search."""
    if q:
        cache_key = make_cache_key("places:search", q=q, lat=lat, lng=lng, radius=radius)
    else:
        cache_key = make_cache_key(
            "places:nearby", lat=lat, lng=lng, radius=radius,
            category=category.value if category else None,
            limit=limit,
        )

    # Check cache — returns list[dict] or None
    cached = await cache_get(redis, cache_key)
    if cached is not None:
        return [_dict_to_response(d) for d in cached]

    # Non-blocking POI loading: kick off background import if area not loaded yet,
    # but return DB results immediately instead of waiting 20-30s for Overpass.
    radius_km = radius / 1000
    if not await is_area_loaded(redis, lat, lng, radius_km):
        background_tasks.add_task(
            _background_load_area, lat, lng, radius_km,
        )

    # Fetch from DB (may be empty on first request; will populate on next request)
    if q:
        places = await place_service.search_places(db, q, lat, lng, radius, limit=limit)
    elif category:
        places = await place_service.find_nearby(
            db, lat, lng, radius_m=radius,
            category=category.value,
            limit=limit,
        )
    else:
        # "All" mode: fetch per-category to prevent popular categories
        # (restaurant, coffee) from crowding out rare ones (hike, museum)
        all_cats = [c.value for c in PlaceCategory]
        per_cat = max(limit // len(all_cats), 10)
        seen_ids: set = set()
        places = []
        for cat in all_cats:
            cat_places = await place_service.find_nearby(
                db, lat, lng, radius_m=radius,
                category=cat, limit=per_cat,
            )
            for p in cat_places:
                if p.id not in seen_ids:
                    seen_ids.add(p.id)
                    places.append(p)
        # Sort combined results by rating
        places.sort(key=lambda p: -(p.rating or 0))

    responses = [_place_to_response(p) for p in places]

    # Only cache if we have data (don't cache empty results during background loading)
    if responses:
        await cache_set(redis, cache_key, [_response_to_dict(r) for r in responses], ttl=get_settings().PLACES_CACHE_TTL)

    return responses


async def _background_load_area(lat: float, lng: float, radius_km: float) -> None:
    """Fire-and-forget POI loading in background."""
    from app.database import async_session_factory
    from app.main import app

    try:
        redis: Redis = app.state.redis
        async with async_session_factory() as db:
            await ensure_places_loaded(db, redis, lat, lng, radius_km=radius_km)
    except Exception:
        logger.exception("Background POI load failed for lat=%.2f lng=%.2f r=%.0fkm", lat, lng, radius_km)


@router.post("/preload", status_code=202)
async def preload_area(
    background_tasks: BackgroundTasks,
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    radius_km: float = Query(default=2.0, ge=0.5, le=50),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Pre-load POIs for an area. Call early when user location is known."""
    if not await is_area_loaded(redis, lat, lng, radius_km):
        background_tasks.add_task(
            _background_load_area, lat, lng, radius_km,
        )
        return {"status": "started"}
    return {"status": "already_loaded"}


@router.get("/{place_id}", response_model=PlaceResponse)
async def get_place(
    place_id: str,
    db: AsyncSession = Depends(get_db),
) -> PlaceResponse:
    """Get place details by ID."""
    place = await place_service.get_place_by_id(db, place_id)
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")
    return _place_to_response(place)


@router.get("/{place_id}/similar", response_model=list[PlaceResponse])
async def get_similar_places(
    place_id: str,
    radius: float = Query(gt=0, le=50000, default=5000),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> list[PlaceResponse]:
    """Get places similar to a given place."""
    cache_key = make_cache_key("places:similar", place_id=place_id, radius=radius)

    cached = await cache_get(redis, cache_key)
    if cached is not None:
        return [_dict_to_response(d) for d in cached]

    places = await place_service.find_similar(db, place_id, radius_m=radius)
    responses = [_place_to_response(p) for p in places]
    await cache_set(redis, cache_key, [_response_to_dict(r) for r in responses], ttl=get_settings().PLACES_CACHE_TTL)
    return responses


@router.post("/{place_id}/save", status_code=204)
async def save_place(
    place_id: str,
    device_id: str = Depends(get_device_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Save a place for a device."""
    await place_service.save_place(db, device_id, place_id)


@router.delete("/{place_id}/save", status_code=204)
async def unsave_place(
    place_id: str,
    device_id: str = Depends(get_device_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a saved place for a device."""
    await place_service.unsave_place(db, device_id, place_id)
