from pydantic import Field

from app.schemas.common import CamelModel, PlaceCategory


class PlaceResponse(CamelModel):
    id: str
    name: str
    category: PlaceCategory
    lat: float
    lng: float
    thumbnail_url: str | None = None
    photo_urls: list[str] = []
    rating: float
    review_count: int
    description: str
    tags: list[str] = []
    best_time_to_visit: str | None = None
    working_hours: str | None = None


class PlaceListParams(CamelModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius: float = Field(gt=0, le=50000, default=10000)
    category: PlaceCategory | None = None
    q: str | None = Field(default=None, min_length=2, max_length=200)
