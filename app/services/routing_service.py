"""Geoapify API client for routing and distance matrix with rate limiting."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
import polyline as polyline_codec

from app.config import get_settings

logger = logging.getLogger(__name__)


class RoutingServiceError(Exception):
    """Raised when routing API calls fail after retries."""


@dataclass
class LatLng:
    lat: float
    lng: float


@dataclass
class LegTime:
    distance_meters: int
    time_seconds: float


@dataclass
class RouteResult:
    encoded_polyline: str
    total_distance_meters: int
    total_time_seconds: float
    leg_times: list[LegTime]


# Semaphore to respect Geoapify rate limit of 5 req/sec (use 4 for safety)
_geoapify_semaphore = asyncio.Semaphore(4)

_RETRYABLE_ERRORS = (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout, httpx.ReadError)
_MAX_RETRIES = 1
_RETRY_DELAY = 2.0


async def get_distance_matrix(
    points: list[LatLng],
) -> list[list[float]]:
    """Get NxN travel time matrix in minutes via Geoapify Route Matrix API."""
    settings = get_settings()
    locations = [{"location": [p.lng, p.lat]} for p in points]

    body = {
        "mode": "walk",
        "sources": locations,
        "targets": locations,
    }

    last_error: Exception | None = None
    async with httpx.AsyncClient() as client:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with _geoapify_semaphore:
                    resp = await client.post(
                        "https://api.geoapify.com/v1/routematrix",
                        params={"apiKey": settings.GEOAPIFY_API_KEY},
                        json=body,
                        timeout=20.0,
                    )
                    resp.raise_for_status()
                    data = resp.json()
            except _RETRYABLE_ERRORS as e:
                last_error = e
                logger.warning("Distance matrix attempt %d failed: %s", attempt + 1, e)
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY)
                    continue
                raise RoutingServiceError(f"Distance matrix API failed after {_MAX_RETRIES + 1} attempts: {e}") from e
            break

    if "sources_to_targets" not in data:
        raise RoutingServiceError(f"Distance matrix response missing 'sources_to_targets': {list(data.keys())}")

    matrix: list[list[float]] = []
    for row in data["sources_to_targets"]:
        matrix.append([cell["time"] / 60.0 for cell in row])

    return matrix


async def get_walking_route(
    waypoints: list[LatLng],
) -> RouteResult:
    """Get walking route with polyline and per-leg times via Geoapify Routing API."""
    settings = get_settings()
    wp_str = "|".join(f"{wp.lat},{wp.lng}" for wp in waypoints)

    last_error: Exception | None = None
    async with httpx.AsyncClient() as client:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with _geoapify_semaphore:
                    resp = await client.get(
                        "https://api.geoapify.com/v1/routing",
                        params={
                            "waypoints": wp_str,
                            "mode": "walk",
                            "apiKey": settings.GEOAPIFY_API_KEY,
                        },
                        timeout=15.0,
                    )
                    resp.raise_for_status()
                    data = resp.json()
            except _RETRYABLE_ERRORS as e:
                last_error = e
                logger.warning("Walking route attempt %d failed: %s", attempt + 1, e)
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY)
                    continue
                raise RoutingServiceError(f"Walking route API failed after {_MAX_RETRIES + 1} attempts: {e}") from e
            break

    features = data.get("features", [])
    if not features:
        raise RoutingServiceError("Walking route response has no features (empty route)")

    feature = features[0]
    props = feature["properties"]
    coords = feature["geometry"]["coordinates"]  # MultiLineString

    leg_times: list[LegTime] = []
    for leg in props["legs"]:
        leg_times.append(
            LegTime(
                distance_meters=leg["distance"],
                time_seconds=leg["time"],
            )
        )

    # Flatten MultiLineString coordinates and encode as polyline
    flat_coords: list[tuple[float, float]] = []
    for line_segment in coords:
        for point in line_segment:
            flat_coords.append((point[1], point[0]))  # (lat, lng)

    encoded = polyline_codec.encode(flat_coords, 5)

    return RouteResult(
        encoded_polyline=encoded,
        total_distance_meters=props["distance"],
        total_time_seconds=props["time"],
        leg_times=leg_times,
    )
