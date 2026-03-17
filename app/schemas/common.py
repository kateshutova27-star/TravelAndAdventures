from enum import Enum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class PlaceCategory(str, Enum):
    nature = "nature"
    city = "city"
    coffee = "coffee"
    restaurant = "restaurant"
    photo = "photo"
    hike = "hike"
    museum = "museum"
    landmark = "landmark"


class AdventureDuration(str, Enum):
    one_hour = "oneHour"
    three_hours = "threeHours"
    half_day = "halfDay"
    full_day = "fullDay"


class SearchRadius(int, Enum):
    km_5 = 5
    km_10 = 10
    km_15 = 15
    km_20 = 20
    km_50 = 50


class GenerationStatus(str, Enum):
    pending = "pending"
    loading_area = "loadingArea"
    finding_places = "findingPlaces"
    building_route = "buildingRoute"
    completed = "completed"
    failed = "failed"
