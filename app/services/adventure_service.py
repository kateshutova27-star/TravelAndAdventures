"""Adventure service: CRUD operations for adventures and stops."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.adventure import Adventure, AdventureStop
from app.models.saved_item import SavedAdventure


async def create_adventure(
    db: AsyncSession,
    device_id: str,
    generation_params: dict,
) -> Adventure:
    """Create a new adventure record with status=pending."""
    adventure = Adventure(
        id=str(uuid.uuid4()),
        status="pending",
        created_by_device_id=device_id,
        generation_params=generation_params,
    )
    db.add(adventure)
    await db.commit()
    await db.refresh(adventure)
    return adventure


async def get_adventure(db: AsyncSession, adventure_id: str) -> Adventure | None:
    """Get adventure with eager-loaded stops and their places."""
    stmt = (
        select(Adventure)
        .where(Adventure.id == adventure_id)
        .options(
            selectinload(Adventure.stops).selectinload(AdventureStop.place)
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_adventure_status(
    db: AsyncSession,
    adventure_id: str,
    status: str,
    error_message: str | None = None,
) -> None:
    """Update the status of an adventure."""
    adventure = await db.get(Adventure, adventure_id)
    if adventure:
        adventure.status = status
        if error_message:
            adventure.error_message = error_message
        adventure.updated_at = datetime.now(timezone.utc)
        await db.commit()


async def set_adventure_result(
    db: AsyncSession,
    adventure_id: str,
    stops_data: list[dict],
    total_time_minutes: int,
    drive_time_minutes: int,
    encoded_polyline: str,
) -> None:
    """Save the completed adventure result with stops."""
    adventure = await db.get(Adventure, adventure_id)
    if not adventure:
        return

    # Clear existing stops
    await db.execute(
        delete(AdventureStop).where(AdventureStop.adventure_id == adventure_id)
    )

    # Create new stops
    for stop_data in stops_data:
        stop = AdventureStop(
            id=str(uuid.uuid4()),
            adventure_id=adventure_id,
            place_id=stop_data["place_id"],
            order=stop_data["order"],
            drive_time_minutes=stop_data["drive_time_minutes"],
            time_to_spend_minutes=stop_data["time_to_spend_minutes"],
        )
        db.add(stop)

    adventure.status = "completed"
    adventure.total_time_minutes = total_time_minutes
    adventure.drive_time_minutes = drive_time_minutes
    adventure.encoded_polyline = encoded_polyline
    adventure.updated_at = datetime.now(timezone.utc)

    await db.commit()
    # Expire the adventure so the next query fetches fresh stops
    # (expire_on_commit=False in session factory means cached stops persist)
    db.expire(adventure)


async def update_adventure_stops(
    db: AsyncSession,
    adventure_id: str,
    stops_data: list[dict],
    total_time_minutes: int,
    drive_time_minutes: int,
    encoded_polyline: str,
) -> Adventure | None:
    """Replace stops and route after an edit operation."""
    await set_adventure_result(
        db, adventure_id, stops_data, total_time_minutes, drive_time_minutes, encoded_polyline
    )
    return await get_adventure(db, adventure_id)


async def get_saved_adventures(
    db: AsyncSession,
    device_id: str,
) -> list[Adventure]:
    """Get all adventures saved by a device."""
    stmt = (
        select(Adventure)
        .join(SavedAdventure, SavedAdventure.adventure_id == Adventure.id)
        .where(SavedAdventure.device_id == device_id)
        .options(
            selectinload(Adventure.stops).selectinload(AdventureStop.place)
        )
        .order_by(Adventure.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def is_adventure_saved(db: AsyncSession, device_id: str, adventure_id: str) -> bool:
    """Check if an adventure is saved by a device."""
    stmt = select(SavedAdventure).where(
        SavedAdventure.device_id == device_id,
        SavedAdventure.adventure_id == adventure_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def save_adventure(db: AsyncSession, device_id: str, adventure_id: str) -> None:
    """Save an adventure for a device. Idempotent."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(SavedAdventure).values(
        id=str(uuid.uuid4()),
        device_id=device_id,
        adventure_id=adventure_id,
    ).on_conflict_do_nothing(
        index_elements=["device_id", "adventure_id"],
    )
    await db.execute(stmt)
    await db.commit()


async def unsave_adventure(db: AsyncSession, device_id: str, adventure_id: str) -> None:
    """Remove a saved adventure."""
    stmt = delete(SavedAdventure).where(
        SavedAdventure.device_id == device_id,
        SavedAdventure.adventure_id == adventure_id,
    )
    await db.execute(stmt)
    await db.commit()
