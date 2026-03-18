"""Adventure generation pipeline: find places, select stops, optimize route."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.place import Place
from app.services.place_service import find_nearby, get_place_by_id
from app.services.poi_loader import _load_area, is_area_loaded
from app.services.route_optimizer import optimize_route
from app.services.routing_service import LatLng, RouteResult, get_distance_matrix, get_walking_route
from app.utils.geo import calculate_search_radius_km, haversine

logger = logging.getLogger(__name__)


# Duration enum value -> time budget in minutes (synced with Flutter client)
DURATION_MINUTES: dict[str, int] = {
    "oneHour": 60,
    "threeHours": 180,
    "halfDay": 300,
    "fullDay": 480,
}


@dataclass
class StopConfig:
    target_stops: int
    min_stops: int
    avg_time_per_stop: int


DURATION_CONFIGS: dict[str, StopConfig] = {
    "oneHour": StopConfig(target_stops=2, min_stops=1, avg_time_per_stop=20),
    "threeHours": StopConfig(target_stops=4, min_stops=2, avg_time_per_stop=30),
    "halfDay": StopConfig(target_stops=5, min_stops=2, avg_time_per_stop=35),
    "fullDay": StopConfig(target_stops=8, min_stops=3, avg_time_per_stop=40),
}


# ── Category-aware visit durations (synced with Flutter visit_duration.dart) ──

# Base visit duration per category in minutes
_BASE_VISIT_DURATION: dict[str, int] = {
    "coffee": 30,
    "restaurant": 60,
    "photo": 15,
    "city": 40,
    "nature": 45,
    "hike": 120,
    "museum": 120,
    "landmark": 25,
}

# Hard minimum per category
_MIN_VISIT_DURATION: dict[str, int] = {
    "coffee": 20,
    "restaurant": 40,
    "photo": 10,
    "city": 15,
    "nature": 20,
    "hike": 60,
    "museum": 60,
    "landmark": 15,
}

# Scale factors by trip duration
_DURATION_SCALE: dict[str, float] = {
    "oneHour": 0.6,
    "threeHours": 1.0,
    "halfDay": 1.0,
    "fullDay": 1.0,
}

# Max stops per category, keyed by duration. Categories not listed = unlimited.
_CATEGORY_MAX: dict[str, dict[str, int]] = {
    "coffee":     {"oneHour": 1, "threeHours": 1, "halfDay": 1, "fullDay": 2},
    "restaurant": {"oneHour": 0, "threeHours": 1, "halfDay": 1, "fullDay": 2},
    "museum":     {"oneHour": 0, "threeHours": 1, "halfDay": 1, "fullDay": 1},
}


def _get_category_limit(category: str, duration: str, categories: list[str] | None = None) -> int | None:
    """Return the max allowed stops for a category at a given duration.

    Returns None if no limit applies (unlimited).
    Special case: if a limited category is the ONLY user-selected category,
    override 0 limits to 1 so the route is not empty.
    """
    limits = _CATEGORY_MAX.get(category)
    if limits is None:
        return None
    limit = limits.get(duration)
    if limit is None:
        return None
    # If limit is 0 but the user selected ONLY this category, allow 1
    if limit == 0 and categories and len(categories) == 1 and categories[0] == category:
        return 1
    return limit


def get_visit_duration(category: str, duration: str) -> int:
    """Return recommended visit time in minutes, scaled by trip duration."""
    base = _BASE_VISIT_DURATION.get(category, 20)
    scale = _DURATION_SCALE.get(duration, 1.0)
    minimum = _MIN_VISIT_DURATION.get(category, 5)
    scaled = round(base * scale)
    return max(minimum, min(scaled, base * 2))


@dataclass
class SelectedStop:
    place: Place
    time_to_spend_minutes: int = 0
    drive_time_minutes: float = 0
    order: int = 0


def _compute_score(
    place: Place,
    anchor_lat: float,
    anchor_lng: float,
    max_radius_m: float,
    is_boosted: bool = False,
) -> float:
    """Score a candidate place (synced with Flutter AdventureBuilder).

    Scoring: rating 60%, proximity 30%, small jitter 10% for variety.
    Boosted (liked) places get a 15% score boost.
    """
    # Rating component (0-5 -> 0-1, weight 60%)
    rating_score = (place.rating or 3.0) / 5.0

    # Proximity component (weight 30%)
    dist = haversine(anchor_lat, anchor_lng, place.lat, place.lng)
    norm_dist = min(dist / max(max_radius_m, 1.0), 1.0)
    proximity_score = 1.0 - norm_dist

    # Small jitter for variety between regenerations (weight 10%)
    jitter = random.uniform(0, 1.0)

    score = rating_score * 0.6 + proximity_score * 0.3 + jitter * 0.1

    if is_boosted:
        score = min(1.0, score * 1.15)

    return score


def _greedy_select(
    scored: list[tuple[Place, float]],
    duration: str,
    time_budget: int,
    anchor_lat: float,
    anchor_lng: float,
    pinned_stops: list[SelectedStop] | None,
    use_minimums: bool = False,
    categories: list[str] | None = None,
) -> list[SelectedStop]:
    """Inner greedy loop: pick stops within time budget.

    If use_minimums=True, all visit times are set to the category minimum
    (second-pass fallback for short trips) and category limits are doubled.
    """
    selected: list[SelectedStop] = list(pinned_stops or [])
    used_ids = {s.place.id for s in selected}
    used_time = sum(s.time_to_spend_minutes for s in selected)

    # Track category counts (including pinned stops)
    category_count: dict[str, int] = {}
    for s in selected:
        cat = s.place.category
        category_count[cat] = category_count.get(cat, 0) + 1

    for place, _ in scored:
        if place.id in used_ids:
            continue

        # Enforce category limits
        cat_limit = _get_category_limit(place.category, duration, categories)
        if cat_limit is not None:
            effective_limit = cat_limit * 2 if use_minimums else cat_limit
            if category_count.get(place.category, 0) >= effective_limit:
                continue

        if use_minimums:
            visit_time = _MIN_VISIT_DURATION.get(place.category, 5)
        else:
            visit_time = get_visit_duration(place.category, duration)

        # Estimate walk time from last stop (haversine * 1.3 / 4.5 km/h)
        # 1.3x correction: real walking paths are ~30% longer than straight line
        if selected:
            last = selected[-1].place
            dist_m = haversine(last.lat, last.lng, place.lat, place.lng)
        else:
            dist_m = haversine(anchor_lat, anchor_lng, place.lat, place.lng)
        walk_min = (dist_m * 1.3 / 1000) / 4.5 * 60

        # Skip if single walk segment > 90 min (unless hike)
        if walk_min > 90 and place.category != "hike":
            continue

        total_needed = walk_min + visit_time
        if used_time + total_needed <= time_budget:
            selected.append(SelectedStop(
                place=place,
                time_to_spend_minutes=visit_time,
            ))
            used_ids.add(place.id)
            used_time += total_needed
            category_count[place.category] = category_count.get(place.category, 0) + 1

    return selected


def select_stops(
    candidates: list[Place],
    categories: list[str],
    duration: str,
    time_budget: int,
    anchor_lat: float,
    anchor_lng: float,
    max_radius_m: float,
    pinned_stops: list[SelectedStop] | None = None,
    boosted_ids: set[str] | None = None,
) -> list[SelectedStop]:
    """Select stops greedily within time budget using category-aware durations.

    Synced with Flutter AdventureBuilder: score by rating+proximity, pick
    greedily by time budget, use category-specific visit durations.
    Two-pass: first with normal durations, fallback with minimum durations.
    """
    _boosted = boosted_ids or set()

    # Filter by radius from anchor
    in_radius = [
        c for c in candidates
        if haversine(anchor_lat, anchor_lng, c.lat, c.lng) <= max_radius_m
    ]

    # Score and sort by quality
    scored = [
        (c, _compute_score(c, anchor_lat, anchor_lng, max_radius_m, is_boosted=str(c.id) in _boosted))
        for c in in_radius
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    # First pass: normal durations
    selected = _greedy_select(
        scored, duration, time_budget, anchor_lat, anchor_lng, pinned_stops,
        categories=categories,
    )

    pinned_count = len(pinned_stops or [])

    # Second pass: if not enough new stops, retry with minimum durations
    # (category limits are doubled in minimums pass)
    if len(selected) - pinned_count < 2 and scored:
        selected2 = _greedy_select(
            scored, duration, time_budget, anchor_lat, anchor_lng,
            pinned_stops, use_minimums=True, categories=categories,
        )
        if len(selected2) > len(selected):
            selected = selected2

    # Edge case: if nothing was added but candidates exist, add the best one
    if len(selected) == pinned_count and scored:
        best_place = scored[0][0]
        used_ids = {s.place.id for s in selected}
        if best_place.id not in used_ids:
            selected.append(SelectedStop(
                place=best_place,
                time_to_spend_minutes=_MIN_VISIT_DURATION.get(best_place.category, 5),
            ))

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
    place_ids: list[str] | None = None,
    radius_km: int | None = None,
    excluded_place_ids: list[str] | None = None,
    boosted_place_ids: list[str] | None = None,
    redis=None,
    update_status_fn=None,
) -> dict:
    """Full adventure generation pipeline.

    Returns dict with: stops, total_time_minutes, drive_time_minutes, encoded_polyline.
    Algorithm synced with Flutter AdventureBuilder.
    """
    duration_minutes = DURATION_MINUTES.get(duration, 180)
    config = DURATION_CONFIGS.get(duration, DURATION_CONFIGS["threeHours"])

    # Step 0: Load pinned places (from trip basket / "add to trip")
    pinned_places: list[Place] = []
    if place_ids:
        for pid in place_ids:
            place = await get_place_by_id(db, pid)
            if place and place.is_active:
                pinned_places.append(place)

    # Step 1: Determine anchor (synced with Flutter: highest-rated basket place)
    anchor_lat, anchor_lng = lat, lng
    if pinned_places:
        # Sort by rating desc, tie-break by distance to user
        pinned_places.sort(key=lambda p: (
            -(p.rating or 0),
            haversine(lat, lng, p.lat, p.lng),
        ))
        anchor_lat = pinned_places[0].lat
        anchor_lng = pinned_places[0].lng

    # Step 2: Calculate radius and find candidates
    if radius_km is not None:
        search_radius_km = float(radius_km)
    else:
        search_radius_km = calculate_search_radius_km(
            duration_minutes, config.target_stops, config.avg_time_per_stop
        )
    radius_m = search_radius_km * 1000

    # Filter pinned places by radius from anchor (far ones stay in basket)
    included_pinned: list[Place] = []
    if pinned_places:
        included_pinned.append(pinned_places[0])  # anchor always included
        for p in pinned_places[1:]:
            if haversine(anchor_lat, anchor_lng, p.lat, p.lng) <= radius_m:
                included_pinned.append(p)

    # DB-first: try existing data before hitting Overpass API
    if update_status_fn:
        await update_status_fn("findingPlaces")

    all_candidates = await find_nearby(db, anchor_lat, anchor_lng, radius_m=radius_m, limit=100)

    # Only fetch from Overpass if not enough candidates in DB
    bg_load_task = None
    if len(all_candidates) < config.min_stops and redis is not None:
        if update_status_fn:
            await update_status_fn("loadingArea")

        core_radius = min(search_radius_km, 1.0)
        await _load_area(
            db, redis, anchor_lat, anchor_lng,
            radius_km=core_radius,
            categories=categories if categories else None,
        )
        all_candidates = await find_nearby(db, anchor_lat, anchor_lng, radius_m=radius_m, limit=100)

        # Still not enough? Try a wider Overpass fetch
        if len(all_candidates) < config.min_stops:
            await _load_area(
                db, redis, anchor_lat, anchor_lng,
                radius_km=min(search_radius_km, 2.0),
                categories=categories if categories else None,
            )
            all_candidates = await find_nearby(db, anchor_lat, anchor_lng, radius_m=radius_m, limit=100)

    # Background: load full radius for future requests
    if redis is not None:
        if not await is_area_loaded(redis, anchor_lat, anchor_lng, search_radius_km):
            bg_load_task = asyncio.create_task(
                _load_area(
                    db, redis, anchor_lat, anchor_lng,
                    radius_km=search_radius_km,
                    categories=categories if categories else None,
                )
            )
            bg_load_task.add_done_callback(
                lambda t: t.exception() and logger.warning("Background full-radius load failed (non-critical)")
                if not t.cancelled() else None
            )

    if update_status_fn:
        await update_status_fn("findingPlaces")

    # Expand radius from DB if still not enough
    if len(all_candidates) < config.min_stops:
        all_candidates = await find_nearby(db, anchor_lat, anchor_lng, radius_m=radius_m * 2, limit=100)
    if len(all_candidates) < config.min_stops:
        all_candidates = await find_nearby(db, anchor_lat, anchor_lng, radius_m=radius_m * 3, limit=100)

    # Filter out excluded places (disliked, recently visited)
    excluded_set = set(excluded_place_ids or [])
    boosted_set = set(boosted_place_ids or [])
    if excluded_set:
        all_candidates = [c for c in all_candidates if str(c.id) not in excluded_set]

    if len(all_candidates) < 1 and len(included_pinned) < 1:
        raise ValueError("Not enough places found nearby to generate an adventure")

    # Step 3: Strict category filtering (synced with Flutter — no padding)
    if categories:
        candidates = [c for c in all_candidates if c.category in categories]
        # Fallback: if strict filtering leaves too few, use all
        if len(candidates) < config.min_stops:
            candidates = all_candidates
    else:
        candidates = all_candidates

    # Step 4: Select stops using greedy time-budget algorithm
    if update_status_fn:
        await update_status_fn("buildingRoute")

    # Build pinned stops with category-aware visit durations
    pinned_ids = {p.id for p in included_pinned}
    pinned_stops: list[SelectedStop] = []
    for place in included_pinned:
        dwell = get_visit_duration(place.category, duration)
        pinned_stops.append(SelectedStop(place=place, time_to_spend_minutes=dwell))

    # Remove pinned places from candidates
    candidates = [c for c in candidates if c.id not in pinned_ids]

    selected = select_stops(
        candidates=candidates,
        categories=categories,
        duration=duration,
        time_budget=duration_minutes,
        anchor_lat=anchor_lat,
        anchor_lng=anchor_lng,
        max_radius_m=radius_m,
        pinned_stops=pinned_stops,
        boosted_ids=boosted_set,
    )

    if len(selected) < 1:
        raise ValueError("Could not select enough stops for this adventure")

    # Step 5: Get distance matrix, optimize route, validate time budget
    async def _build_route(stops: list[SelectedStop]) -> tuple[list[SelectedStop], RouteResult]:
        """Optimize stop order and get walking route."""
        pts = [LatLng(s.place.lat, s.place.lng) for s in stops]
        mat = await get_distance_matrix(pts)
        order = optimize_route(mat, start_idx=0)
        ordered = [stops[0]] + [stops[i] for i in order]
        wps = [LatLng(s.place.lat, s.place.lng) for s in ordered]
        rt = await get_walking_route(wps)
        # Assign walk times
        for i, stop in enumerate(ordered):
            if i > 0 and (i - 1) < len(rt.leg_times):
                stop.drive_time_minutes = rt.leg_times[i - 1].time_seconds / 60.0
            else:
                stop.drive_time_minutes = 0
            stop.order = i + 1
        return ordered, rt

    # Single-stop adventure: skip routing API (Geoapify needs 2+ waypoints)
    if len(selected) == 1:
        selected[0].order = 1
        selected[0].drive_time_minutes = 0
        total_dwell = selected[0].time_to_spend_minutes

        return {
            "stops": [{
                "place_id": selected[0].place.id,
                "order": 1,
                "drive_time_minutes": 0,
                "time_to_spend_minutes": selected[0].time_to_spend_minutes,
            }],
            "total_time_minutes": total_dwell,
            "drive_time_minutes": 0,
            "encoded_polyline": "",
        }

    selected, route = await _build_route(selected)

    # Step 6: Validate time budget — trim dwell, remove stops with long legs
    max_iterations = len(selected) + 3
    for _ in range(max_iterations):
        if _validate_time_budget(selected, route.total_time_seconds, duration_minutes):
            break

        total_dwell = sum(s.time_to_spend_minutes for s in selected)
        total_travel_min = route.total_time_seconds / 60.0
        overshoot = (total_dwell + total_travel_min) - duration_minutes

        # Find the leg with the longest walk time
        max_leg_time = 0
        max_leg_stop_idx = -1
        for i, stop in enumerate(selected):
            if stop.drive_time_minutes > max_leg_time:
                max_leg_time = stop.drive_time_minutes
                max_leg_stop_idx = i

        # Max acceptable leg time: travel budget / number of legs
        travel_budget = duration_minutes - total_dwell
        num_legs = max(len(selected) - 1, 1)
        max_acceptable_leg = max(travel_budget / num_legs * 1.5, 15)

        # Strategy 1: if there's a leg way too long, remove that stop and rebuild
        if max_leg_time > max_acceptable_leg and len(selected) > 2:
            removed = selected.pop(max_leg_stop_idx)
            logger.info(
                "Removing stop %s (leg %.0f min > max %.0f min)",
                removed.place.name, max_leg_time, max_acceptable_leg,
            )
            selected, route = await _build_route(selected)
            continue

        # Strategy 1b: only 2 stops but walk leg is absurdly long — drop
        # the non-pinned stop to produce a single-stop adventure
        if max_leg_time > max_acceptable_leg and len(selected) == 2:
            pinned_id_set = {p.id for p in included_pinned}
            non_pinned = [i for i, s in enumerate(selected) if s.place.id not in pinned_id_set]
            if non_pinned:
                removed = selected.pop(non_pinned[0])
                logger.info(
                    "Dropping non-pinned stop %s (leg %.0f min too long for 2-stop route)",
                    removed.place.name, max_leg_time,
                )
                # Fall through to single-stop return below
                break

        # Strategy 2: trim dwell times proportionally (respect category minimums)
        if overshoot > 0 and overshoot <= total_dwell * 0.5:
            scale = (total_dwell - overshoot) / total_dwell
            for stop in selected:
                minimum = _MIN_VISIT_DURATION.get(stop.place.category, 5)
                stop.time_to_spend_minutes = max(minimum, int(stop.time_to_spend_minutes * scale))
            continue

        # Strategy 3: remove the weakest non-pinned stop and rebuild
        if len(selected) > 2:
            pinned_id_set = {p.id for p in included_pinned}
            removable = [s for s in selected if s.place.id not in pinned_id_set]
            if not removable:
                removable = list(selected)
            weakest = min(
                removable,
                key=lambda s: _compute_score(s.place, anchor_lat, anchor_lng, radius_m),
            )
            selected.remove(weakest)
            selected, route = await _build_route(selected)
            continue

        # Strategy 4: only 2 stops left, trim to minimums
        if overshoot > 0:
            for stop in selected:
                minimum = _MIN_VISIT_DURATION.get(stop.place.category, 5)
                stop.time_to_spend_minutes = minimum
            break

    # If trimming reduced to 1 stop, return as single-stop adventure
    if len(selected) == 1:
        selected[0].order = 1
        selected[0].drive_time_minutes = 0
        total_dwell = selected[0].time_to_spend_minutes

        return {
            "stops": [{
                "place_id": selected[0].place.id,
                "order": 1,
                "drive_time_minutes": 0,
                "time_to_spend_minutes": selected[0].time_to_spend_minutes,
            }],
            "total_time_minutes": total_dwell,
            "drive_time_minutes": 0,
            "encoded_polyline": "",
        }

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
