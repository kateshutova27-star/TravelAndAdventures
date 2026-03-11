"""Overpass API client for fetching POI data from OpenStreetMap."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


@dataclass
class BBox:
    south: float
    west: float
    north: float
    east: float

    def to_str(self) -> str:
        return f"{self.south},{self.west},{self.north},{self.east}"


CATEGORY_QUERIES: dict[str, str] = {
    "nature": """
        node["leisure"="park"]({bbox});
        way["leisure"="park"]({bbox});
        relation["leisure"="park"]({bbox});
        node["leisure"="garden"]({bbox});
        way["leisure"="garden"]({bbox});
        node["leisure"="nature_reserve"]({bbox});
        way["leisure"="nature_reserve"]({bbox});
        relation["leisure"="nature_reserve"]({bbox});
        node["waterway"="waterfall"]({bbox});
        node["natural"="beach"]({bbox});
        way["natural"="beach"]({bbox});
    """,
    "city": """
        node["historic"="monument"]({bbox});
        node["historic"="memorial"]({bbox});
        node["tourism"="attraction"]({bbox});
        way["tourism"="attraction"]({bbox});
        node["historic"="building"]({bbox});
        way["historic"="building"]({bbox});
        node["historic"="castle"]({bbox});
        way["historic"="castle"]({bbox});
        node["historic"="ruins"]({bbox});
        node["tourism"="viewpoint"]({bbox});
        node["tourism"="artwork"]({bbox});
        way["tourism"="artwork"]({bbox});
        way["highway"="pedestrian"]["name"]({bbox});
        node["place"="square"]({bbox});
        way["place"="square"]({bbox});
    """,
    "coffee": """
        node["amenity"="cafe"]({bbox});
        way["amenity"="cafe"]({bbox});
        node["shop"="coffee"]({bbox});
    """,
    "restaurant": """
        node["amenity"="restaurant"]({bbox});
        way["amenity"="restaurant"]({bbox});
        node["amenity"="bar"]({bbox});
        way["amenity"="bar"]({bbox});
        node["amenity"="food_court"]({bbox});
    """,
    "photo": """
        node["tourism"="viewpoint"]({bbox});
        node["man_made"="tower"]["tower:type"="observation"]({bbox});
        way["man_made"="tower"]["tower:type"="observation"]({bbox});
    """,
}


async def fetch_category(
    client: httpx.AsyncClient,
    category: str,
    bbox: BBox,
) -> list[dict]:
    """Fetch POI elements from Overpass API for a single category."""
    body_clauses = CATEGORY_QUERIES[category].format(bbox=bbox.to_str())
    query = f"[out:json][timeout:120];({body_clauses});out center tags;"

    logger.info("Fetching %s for bbox %s", category, bbox.to_str())
    resp = await client.post(OVERPASS_URL, data={"data": query}, timeout=130)
    resp.raise_for_status()
    elements = resp.json().get("elements", [])
    logger.info("Fetched %d elements for %s", len(elements), category)
    return elements


async def fetch_all_categories(bbox: BBox) -> dict[str, list[dict]]:
    """Fetch all categories sequentially (respecting Overpass rate limits)."""
    results: dict[str, list[dict]] = {}
    async with httpx.AsyncClient() as client:
        for category in CATEGORY_QUERIES:
            try:
                results[category] = await fetch_category(client, category, bbox)
            except httpx.HTTPError as e:
                logger.error("Failed to fetch %s: %s", category, e)
                results[category] = []
            # Respect Overpass rate limit (~1 req/sec)
            await asyncio.sleep(5)
    return results
