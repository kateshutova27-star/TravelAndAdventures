"""Adventure generation pipeline: find places, select stops, optimize route."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.place import Place
from app.services.place_service import find_nearby
from app.services.route_optimizer import optimize_route
from app.services.routing_service import LatLng, get_distance_matrix, get_walking_route
from app.utils.geo import calculate_search_radius_km

logger = logging.getLogger(__name__)


# Duration enum value -> minutes
DURATION_MINUTES: dict[str, int] = {
    "oneHour": 60,
    "threeHours": 180,
    "halfDay": 360,
    "fullDay": 720,
}


@dataclass
class StopConfig:
    target_stops: int
    min_stops: int
    slot_template: list[str]
    avg_time_per_stop: int


DURATION_CONFIGS: dict[str, StopConfig] = {
    "oneHour": StopConfig(
        target_stops=3, min_stops=2,
        slot_template=["wow_spot", "food_coffee", "secondary"],
        avg_time_per_stop=12,
    ),
    "threeHours": StopConfig(
        target_stops=5, min_stops=4,
        slot_template=["wow_spot", "food_coffee", "secondary", "viewpoint", "secondary"],
        avg_time_per_stop=18,
    ),
    "halfDay": StopConfig(
        target_stops=7, min_stops=5,
        slot_template=["wow_spot", "food_coffee", "secondary", "viewpoint", "secondary", "food_coffee", "secondary"],
        avg_time_per_stop=25,
    ),
    "fullDay": StopConfig(
        target_stops=10, min_stops=7,
        slot_template=[
            "wow_spot", "food_coffee", "secondary", "viewpoint", "secondary",
            "food_coffee", "secondary", "viewpoint", "secondary", "food_coffee",
        ],
        avg_time_per_stop=30,
    ),
}

# Which slot types each place category can fill
CATEGORY_SLOT_MAP: dict[str, list[str]] = {
    "nature": ["wow_spot", "secondary", "viewpoint"],
    "city": ["wow_spot", "secondary"],
    "coffee": ["food_coffee"],
    "restaurant": ["food_coffee"],
    "photo": ["viewpoint", "secondary"],
}

# Default dwell times per slot type
DWELL_TIMES: dict[str, int] = {
    "wow_spot": 30,
    "secondary": 15,
    "food_coffee": 20,
    "viewpoint": 10,
}


@dataclass
class SelectedStop:
    place: Place
    slot_type: str
    time_to_spend_minutes: int = 0
    drive_time_minutes: float = 0
    order: int = 0


def _compute_score(place: Place, requested_categories: list[str]) -> float:
    """Score a candidate place for selection."""
    score = 0.0
    # Rating component (0-5 -> 0-1, weight 40%)
    score += (place.rating or 3.0) / 5.0 * 0.4
    # Category match (weight 30%)
    if place.category in requested_categories:
        score += 0.3
    # Popularity via review count (weight 15%)
    review_score = min(math.log10(max(place.review_count or 1, 1)) / 4.0, 1.0)
    score += review_score * 0.15
    # Base uniqueness bonus (weight 15%)
    score += 0.1
    return score


def _get_compatible_slots(category: str) -> list[str]:
    return CATEGORY_SLOT_MAP.get(category, ["secondary"])


def select_stops(
    candidates: list[Place],
    categories: list[str],
    config: StopConfig,
) -> list[SelectedStop]:
    """Select stops by greedily filling slot template with diversity constraint."""
    # Score all candidates
    scored = [(c, _compute_score(c, categories)) for c in candidates]

    # Bucket by compatible slot types, sorted by score desc
    buckets: dict[str, list[tuple[Place, float]]] = {
        "wow_spot": [], "secondary": [], "food_coffee": [], "viewpoint": [],
    }
    for place, score in scored:
        for slot_type in _get_compatible_slots(place.category):
            if slot_type in buckets:
                buckets[slot_type].append((place, score))

    for bucket in buckets.values():
        bucket.sort(key=lambda x: x[1], reverse=True)

    # Fill slots greedily
    selected: list[SelectedStop] = []
    used_ids: set[str] = set()
    last_category: str | None = None

    for slot_type in config.slot_template:
        best: Place | None = None

        # First pass: respect diversity constraint
        for place, _ in buckets.get(slot_type, []):
            if place.id in used_ids:
                continue
            if place.category == last_category:
                continue
            best = place
            break

        # Second pass: relax diversity
        if best is None:
            for place, _ in buckets.get(slot_type, []):
                if place.id not in used_ids:
                    best = place
                    break

        if best is None:
            continue

        used_ids.add(best.id)
        last_category = best.category

        dwell = DWELL_TIMES.get(slot_type, 15)
        selected.append(SelectedStop(place=best, slot_type=slot_type, time_to_spend_minutes=dwell))

    return selected


def _validate_time_budget(
    stops: list[SelectedStop],
    total_travel_seconds: float,
    duration_minutes: int,
) -> bool:
    """Check if adventure fits within the time budget (with 10% buffer)."""
    total_dwell = sum(s.time_to_spend_minutes for s in stops)
    total_travel_min = total_travel_seconds / 60.0
    total_time = total_dwell + total_travel_min
    budget = duration_minutes * 1.10
    return total_time <= budget


async def generate_adventure(
    db: AsyncSession,
    lat: float,
    lng: float,
    duration: str,
    categories: list[str],
    update_status_fn=None,
) -> dict:
    """Full adventure generation pipeline.

    Returns dict with: stops, total_time_minutes, drive_time_minutes, encoded_polyline.
    """
    duration_minutes = DURATION_MINUTES.get(duration, 180)
    config = DURATION_CONFIGS.get(duration, DURATION_CONFIGS["threeHours"])

    # Step 1: Calculate radius and find candidates
    if update_status_fn:
        await update_status_fn("findingPlaces")

    radius_km = calculate_search_radius_km(
        duration_minutes, config.target_stops, config.avg_time_per_stop
    )
    radius_m = radius_km * 1000

    all_candidates = await find_nearby(db, lat, lng, radius_m=radius_m, limit=100)

    # Expand radius if not enough candidates overall
    if len(all_candidates) < config.min_stops:
        all_candidates = await find_nearby(db, lat, lng, radius_m=radius_m * 2, limit=100)
    if len(all_candidates) < config.min_stops:
        all_candidates = await find_nearby(db, lat, lng, radius_m=radius_m * 3, limit=100)

    if len(all_candidates) < 2:
        raise ValueError("Not enough places found nearby to generate an adventure")

    # Build candidates: prefer requested categories, but always include
    # enough variety to fill all slot types in the template.
    if categories:
        preferred = [c for c in all_candidates if c.category in categories]
        others = [c for c in all_candidates if c.category not in categories]
        # Always include non-category places so slot types like wow_spot /
        # secondary / viewpoint can be filled even when user picks only coffee.
        candidates = preferred + others
    else:
        candidates = all_candidates

    # Step 2: Select stops
    if update_status_fn:
        await update_status_fn("buildingRoute")

    selected = select_stops(candidates, categories, config)

    if len(selected) < 2:
        raise ValueError("Could not select enough stops for this adventure")

    # Step 3: Get distance matrix and optimize route
    all_points = [LatLng(lat, lng)] + [LatLng(s.place.lat, s.place.lng) for s in selected]

    matrix = await get_distance_matrix(all_points)
    optimized_order = optimize_route(matrix, start_idx=0)

    # Reorder stops: optimized_order contains indices into all_points[1:], so offset by -1
    selected = [selected[i - 1] for i in optimized_order]

    # Step 4: Get walking route
    waypoints = [LatLng(lat, lng)] + [LatLng(s.place.lat, s.place.lng) for s in selected]
    route = await get_walking_route(waypoints)

    # Assign drive times from route legs
    for i, stop in enumerate(selected):
        if i < len(route.leg_times):
            stop.drive_time_minutes = route.leg_times[i].time_seconds / 60.0
        stop.order = i + 1

    # Step 5: Validate time budget (up to 3 iterations)
    for _ in range(3):
        if _validate_time_budget(selected, route.total_time_seconds, duration_minutes):
            break

        # Strategy 1: trim dwell times proportionally
        total_dwell = sum(s.time_to_spend_minutes for s in selected)
        overshoot = (total_dwell + route.total_time_seconds / 60.0) - duration_minutes
        if overshoot > 0 and overshoot < total_dwell * 0.3:
            scale = (total_dwell - overshoot) / total_dwell
            for stop in selected:
                stop.time_to_spend_minutes = max(5, int(stop.time_to_spend_minutes * scale))
            continue

        # Strategy 2: remove weakest optional stop
        optional = [s for s in selected if s.slot_type in ("viewpoint", "secondary")]
        if optional:
            weakest = min(optional, key=lambda s: _compute_score(s.place, categories))
            selected.remove(weakest)
            # Rebuild route with fewer stops
            waypoints = [LatLng(lat, lng)] + [LatLng(s.place.lat, s.place.lng) for s in selected]
            route = await get_walking_route(waypoints)
            for i, stop in enumerate(selected):
                if i < len(route.leg_times):
                    stop.drive_time_minutes = route.leg_times[i].time_seconds / 60.0
                stop.order = i + 1

    # Build result
    total_dwell = sum(s.time_to_spend_minutes for s in selected)
    total_drive = route.total_time_seconds / 60.0

    stops_data = [
        {
            "place_id": s.place.id,
            "order": s.order,
            "drive_time_minutes": round(s.drive_time_minutes),
            "time_to_spend_minutes": s.time_to_spend_minutes,
        }
        for s in selected
    ]

    return {
        "stops": stops_data,
        "total_time_minutes": round(total_dwell + total_drive),
        "drive_time_minutes": round(total_drive),
        "encoded_polyline": route.encoded_polyline,
    }
