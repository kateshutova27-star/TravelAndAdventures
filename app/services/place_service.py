"""Place service: PostGIS spatial queries, text search, similar places."""

from __future__ import annotations

import uuid

from geoalchemy2.types import Geography
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.place import Place
from app.models.saved_item import SavedPlace


def _ref_geog(lng: float, lat: float):
    """Build a reference geography point for spatial queries."""
    return func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326).cast(Geography)


async def find_nearby(
    db: AsyncSession,
    lat: float,
    lng: float,
    radius_m: float = 10_000,
    category: str | None = None,
    limit: int = 50,
) -> list[Place]:
    """Find active places within radius_m meters of (lat, lng), ordered by distance."""
    ref = _ref_geog(lng, lat)

    stmt = (
        select(
            Place,
            func.ST_Distance(Place.geog, ref).label("distance_m"),
        )
        .where(func.ST_DWithin(Place.geog, ref, radius_m))
        .where(Place.is_active.is_(True))
    )

    if category:
        stmt = stmt.where(Place.category == category)

    stmt = stmt.order_by("distance_m").limit(limit)

    result = await db.execute(stmt)
    return [row.Place for row in result.all()]


async def search_places(
    db: AsyncSession,
    query: str,
    lat: float | None = None,
    lng: float | None = None,
    radius_m: float = 50_000,
    limit: int = 20,
) -> list[Place]:
    """Search places by name using trigram similarity."""
    stmt = (
        select(Place)
        .where(Place.is_active.is_(True))
        .where(func.similarity(Place.name, query) > 0.3)
    )

    if lat is not None and lng is not None:
        ref = _ref_geog(lng, lat)
        stmt = stmt.where(func.ST_DWithin(Place.geog, ref, radius_m))

    stmt = stmt.order_by(func.similarity(Place.name, query).desc()).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_place_by_id(db: AsyncSession, place_id: str) -> Place | None:
    """Get a single place by ID."""
    return await db.get(Place, place_id)


async def find_similar(
    db: AsyncSession,
    place_id: str,
    radius_m: float = 5_000,
    limit: int = 10,
) -> list[Place]:
    """Find similar places: same category, nearby, ordered by rating."""
    target = await db.get(Place, place_id)
    if not target:
        return []

    ref = _ref_geog(target.lng, target.lat)

    stmt = (
        select(Place)
        .where(Place.id != place_id)
        .where(Place.category == target.category)
        .where(Place.is_active.is_(True))
        .where(func.ST_DWithin(Place.geog, ref, radius_m))
        .order_by(Place.rating.desc().nullslast())
        .limit(limit)
    )

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def save_place(db: AsyncSession, device_id: str, place_id: str) -> None:
    """Save a place for a device. Idempotent (upsert)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(SavedPlace).values(
        id=str(uuid.uuid4()),
        device_id=device_id,
        place_id=place_id,
    ).on_conflict_do_nothing(
        index_elements=["device_id", "place_id"],
    )
    await db.execute(stmt)
    await db.commit()


async def unsave_place(db: AsyncSession, device_id: str, place_id: str) -> None:
    """Remove a saved place for a device."""
    from sqlalchemy import delete

    stmt = delete(SavedPlace).where(
        SavedPlace.device_id == device_id,
        SavedPlace.place_id == place_id,
    )
    await db.execute(stmt)
    await db.commit()
