"""Geographic utility functions: haversine distance, polyline encoding."""

import math


EARTH_RADIUS_M = 6_371_000  # Earth radius in meters


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return distance in meters between two geographic points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))


WALKING_SPEED_KMH = 4.5
BUFFER_FACTOR = 0.6  # crow-fly to actual route ratio


def calculate_search_radius_km(duration_minutes: int, target_stops: int, avg_stop_minutes: int) -> float:
    """Calculate crow-fly search radius in km based on duration budget."""
    travel_budget_min = duration_minutes - (target_stops * avg_stop_minutes)
    if travel_budget_min <= 0:
        return 0.5

    total_travel_km = (travel_budget_min / 60) * WALKING_SPEED_KMH
    radius_km = total_travel_km * 0.5
    return max(2.0, min(radius_km, 25.0))
