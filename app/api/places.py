"""Places API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_device_id, get_redis
from app.schemas.common import PlaceCategory
from app.schemas.place import PlaceResponse
from app.services import place_service
from app.utils.cache import cache_get, cache_set, make_cache_key

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
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    radius: float = Query(gt=0, le=50000, default=10000),
    category: PlaceCategory | None = Query(default=None),
    q: str | None = Query(default=None, min_length=2, max_length=200),
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
        )

    # Check cache — returns list[dict] or None
    cached = await cache_get(redis, cache_key)
    if cached is not None:
        return [_dict_to_response(d) for d in cached]

    # Fetch from DB
    if q:
        places = await place_service.search_places(db, q, lat, lng, radius)
    else:
        places = await place_service.find_nearby(
            db, lat, lng, radius_m=radius,
            category=category.value if category else None,
        )

    responses = [_place_to_response(p) for p in places]

    # Cache serialized dicts
    await cache_set(redis, cache_key, [_response_to_dict(r) for r in responses], ttl=300)

    return responses


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
    await cache_set(redis, cache_key, [_response_to_dict(r) for r in responses], ttl=300)
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
