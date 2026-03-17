from datetime import datetime

from pydantic import Field

from app.schemas.common import (
    AdventureDuration,
    CamelModel,
    GenerationStatus,
    PlaceCategory,
    SearchRadius,
)
from app.schemas.place import PlaceResponse


class AdventureStopResponse(CamelModel):
    place: PlaceResponse
    drive_time_minutes: int
    time_to_spend_minutes: int
    order: int


class AdventureResponse(CamelModel):
    id: str
    stops: list[AdventureStopResponse] = []
    total_time_minutes: int
    drive_time_minutes: int
    encoded_polyline: str | None = None
    created_at: datetime | None = None


class GenerateAdventureRequest(CamelModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    duration: AdventureDuration
    categories: list[PlaceCategory] = []
    place_ids: list[str] = []
    radius_km: SearchRadius | None = None
    excluded_place_ids: list[str] = []
    boosted_place_ids: list[str] = []


class GenerateAdventureResponse(CamelModel):
    task_id: str = Field(serialization_alias="task_id")


class AdventureStatusResponse(CamelModel):
    status: GenerationStatus
    adventure_id: str | None = Field(default=None, serialization_alias="adventure_id")
    error_message: str | None = None


class AdventureUpdateRequest(CamelModel):
    stops: list[AdventureStopResponse]
