"""Tests for adventure service: create, get, update, save/unsave logic.

These tests mock the database session to verify service-layer logic
without requiring a real PostgreSQL connection.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.adventure_service import (
    create_adventure,
    get_adventure,
    set_adventure_result,
    update_adventure_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_adventure(**overrides):
    defaults = {
        "id": str(uuid.uuid4()),
        "status": "pending",
        "created_by_device_id": "device-1",
        "generation_params": {"lat": 49.28, "lng": -123.12},
        "stops": [],
        "total_time_minutes": 0,
        "drive_time_minutes": 0,
        "encoded_polyline": None,
        "error_message": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    obj = MagicMock()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


def _mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.get = AsyncMock()
    db.execute = AsyncMock()
    db.expire = MagicMock()
    return db


# ---------------------------------------------------------------------------
# create_adventure
# ---------------------------------------------------------------------------


class TestCreateAdventure:
    @pytest.mark.asyncio
    async def test_creates_adventure_with_pending_status(self):
        db = _mock_db()
        adventure = await create_adventure(db, "device-1", {"lat": 49.28})

        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.status == "pending"
        assert added.created_by_device_id == "device-1"
        assert added.generation_params == {"lat": 49.28}
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generates_uuid_id(self):
        db = _mock_db()
        await create_adventure(db, "device-1", {})
        added = db.add.call_args[0][0]
        # Should be a valid UUID string
        uuid.UUID(added.id)


# ---------------------------------------------------------------------------
# update_adventure_status
# ---------------------------------------------------------------------------


class TestUpdateAdventureStatus:
    @pytest.mark.asyncio
    async def test_updates_status(self):
        adventure = _fake_adventure()
        db = _mock_db()
        db.get = AsyncMock(return_value=adventure)

        await update_adventure_status(db, adventure.id, "buildingRoute")

        assert adventure.status == "buildingRoute"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sets_error_message(self):
        adventure = _fake_adventure()
        db = _mock_db()
        db.get = AsyncMock(return_value=adventure)

        await update_adventure_status(
            db, adventure.id, "failed", error_message="Something broke"
        )

        assert adventure.status == "failed"
        assert adventure.error_message == "Something broke"

    @pytest.mark.asyncio
    async def test_does_nothing_for_missing_adventure(self):
        db = _mock_db()
        db.get = AsyncMock(return_value=None)

        # Should not raise
        await update_adventure_status(db, "nonexistent-id", "completed")
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# set_adventure_result
# ---------------------------------------------------------------------------


class TestSetAdventureResult:
    @pytest.mark.asyncio
    async def test_saves_stops_and_metadata(self):
        adventure = _fake_adventure()
        db = _mock_db()
        db.get = AsyncMock(return_value=adventure)

        stops_data = [
            {
                "place_id": "place-1",
                "order": 1,
                "drive_time_minutes": 5,
                "time_to_spend_minutes": 20,
            },
            {
                "place_id": "place-2",
                "order": 2,
                "drive_time_minutes": 10,
                "time_to_spend_minutes": 30,
            },
        ]

        await set_adventure_result(
            db,
            adventure.id,
            stops_data=stops_data,
            total_time_minutes=65,
            drive_time_minutes=15,
            encoded_polyline="encoded123",
        )

        assert adventure.status == "completed"
        assert adventure.total_time_minutes == 65
        assert adventure.drive_time_minutes == 15
        assert adventure.encoded_polyline == "encoded123"
        # Should have added 2 stops
        assert db.add.call_count == 2
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_nothing_for_missing_adventure(self):
        db = _mock_db()
        db.get = AsyncMock(return_value=None)

        await set_adventure_result(
            db, "nonexistent", [], 0, 0, ""
        )
        # Should not commit if adventure not found
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clears_existing_stops_before_saving(self):
        adventure = _fake_adventure()
        db = _mock_db()
        db.get = AsyncMock(return_value=adventure)

        await set_adventure_result(
            db, adventure.id, [], 0, 0, ""
        )

        # Should execute DELETE for old stops
        db.execute.assert_awaited()

    @pytest.mark.asyncio
    async def test_expires_adventure_after_save(self):
        adventure = _fake_adventure()
        db = _mock_db()
        db.get = AsyncMock(return_value=adventure)

        await set_adventure_result(
            db, adventure.id, [], 0, 0, ""
        )

        db.expire.assert_called_once_with(adventure)


# ---------------------------------------------------------------------------
# get_adventure
# ---------------------------------------------------------------------------


class TestGetAdventure:
    @pytest.mark.asyncio
    async def test_returns_adventure_when_found(self):
        adventure = _fake_adventure()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = adventure
        db = _mock_db()
        db.execute = AsyncMock(return_value=mock_result)

        result = await get_adventure(db, adventure.id)
        assert result is adventure

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db = _mock_db()
        db.execute = AsyncMock(return_value=mock_result)

        result = await get_adventure(db, "nonexistent")
        assert result is None
