"""On-demand POI loader: fetches places from OSM when a new area is requested."""

from __future__ import annotations

import logging

from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from redis.asyncio import Redis
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.place import Place
from scripts.enrichment import transform_element
from scripts.osm_fetcher import BBox, fetch_all_categories

logger = logging.getLogger(__name__)

# 2 weeks in seconds
AREA_CACHE_TTL = 1_209_600


def _area_key(lat: float, lng: float, radius_km: float) -> str:
    """Redis key for a loaded area. Rounds coords to ~11km grid (1 decimal)."""
    lat_r = round(lat, 1)
    lng_r = round(lng, 1)
    return f"area_loaded:{lat_r}:{lng_r}:{int(radius_km)}"


async def is_area_loaded(redis: Redis, lat: float, lng: float, radius_km: float) -> bool:
    """Check if the area (or a larger covering area) is already loaded."""
    # Check exact match first
    if await redis.exists(_area_key(lat, lng, radius_km)):
        return True
    # Check if a larger radius is already loaded for the same grid cell
    for larger in (radius_km + 5, radius_km + 10, radius_km + 15):
        if await redis.exists(_area_key(lat, lng, larger)):
            return True
    return False


def _bbox_from_center(lat: float, lng: float, radius_km: float) -> BBox:
    """Create a bounding box from center point and radius in km."""
    # 1 degree lat ≈ 111 km, 1 degree lng ≈ 111 * cos(lat) km
    import math
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return BBox(
        south=lat - dlat,
        west=lng - dlng,
        north=lat + dlat,
        east=lng + dlng,
    )


async def _load_area(
    db: AsyncSession,
    redis: Redis,
    lat: float,
    lng: float,
    radius_km: float,
    categories: list[str] | None = None,
) -> bool:
    """Load POIs for a single area radius. Returns True if data was fetched."""
    key = _area_key(lat, lng, radius_km)

    if await redis.exists(key):
        return False

    lock_key = f"{key}:loading"
    acquired = await redis.set(lock_key, "1", ex=180, nx=True)
    if not acquired:
        return False

    try:
        logger.info("Loading POIs for area lat=%.2f lng=%.2f r=%dkm", lat, lng, radius_km)

        bbox = _bbox_from_center(lat, lng, radius_km)
        raw_data = await fetch_all_categories(bbox, categories=categories)

        all_places: list[dict] = []
        for category, elements in raw_data.items():
            for el in elements:
                place = transform_element(el, category)
                if place:
                    all_places.append(place)

        logger.info("Fetched %d places from OSM for r=%dkm", len(all_places), radius_km)

        if all_places:
            await _upsert_places(db, all_places)

        await redis.set(key, "1", ex=AREA_CACHE_TTL)
        return True

    except Exception:
        logger.exception("Failed to load POIs for area lat=%.2f lng=%.2f r=%dkm", lat, lng, radius_km)
        return False
    finally:
        await redis.delete(lock_key)


async def ensure_places_loaded(
    db: AsyncSession,
    redis: Redis,
    lat: float,
    lng: float,
    radius_km: float = 10.0,
    categories: list[str] | None = None,
) -> None:
    """Two-phase loading: fetch a small core area first (fast), then full radius.

    Phase 1: Load a 2km radius — enough for initial adventure generation (~8-12s).
    Phase 2: Load the full requested radius if bigger (~15-20s, but generation
             can already proceed with phase-1 data).
    """
    CORE_RADIUS_KM = 2.0

    # Skip if this area (or a larger one) is already loaded
    if await is_area_loaded(redis, lat, lng, radius_km):
        return

    # Phase 1: fast core load (small bbox = fast Overpass response)
    if radius_km > CORE_RADIUS_KM:
        if not await is_area_loaded(redis, lat, lng, CORE_RADIUS_KM):
            await _load_area(db, redis, lat, lng, CORE_RADIUS_KM, categories)

    # Phase 2: full radius
    await _load_area(db, redis, lat, lng, radius_km, categories)


async def _upsert_places(db: AsyncSession, places: list[dict]) -> int:
    """Batch upsert place dicts into database (chunks of 200)."""
    from datetime import datetime, timezone

    BATCH_SIZE = 200
    count = 0

    for i in range(0, len(places), BATCH_SIZE):
        batch = places[i : i + BATCH_SIZE]
        rows = []
        for place in batch:
            rows.append({
                "osm_id": place["osm_id"],
                "osm_type": place["osm_type"],
                "name": place["name"],
                "category": place["category"],
                "lat": place["lat"],
                "lng": place["lng"],
                "geog": ST_SetSRID(ST_MakePoint(place["lng"], place["lat"]), 4326),
                "description": place.get("description", ""),
                "tags": place.get("tags", []),
                "working_hours": place.get("working_hours"),
                "best_time_to_visit": place.get("best_time_to_visit"),
                "rating": place.get("rating"),
                "review_count": place.get("review_count", 0),
                "thumbnail_url": place.get("thumbnail_url"),
                "photo_urls": place.get("photo_urls", []),
                "images_fetched_at": datetime.now(timezone.utc) if place.get("thumbnail_url") else None,
            })

        stmt = pg_insert(Place).values(rows).on_conflict_do_update(
            constraint="uq_places_osm_id_osm_type",
            set_={
                "name": pg_insert(Place).excluded.name,
                "description": pg_insert(Place).excluded.description,
                "tags": pg_insert(Place).excluded.tags,
                "working_hours": pg_insert(Place).excluded.working_hours,
                "updated_at": func.now(),
            },
        )
        await db.execute(stmt)
        count += len(batch)

    await db.commit()
    logger.info("Upserted %d places in %d batches", count, (len(places) + BATCH_SIZE - 1) // BATCH_SIZE)
    return count
