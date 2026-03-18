"""Overpass API client for fetching POI data from OpenStreetMap."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


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
        node["natural"="volcano"]({bbox});
        way["natural"="wood"]["name"]({bbox});
        relation["natural"="wood"]["name"]({bbox});
        node["tourism"="camp_site"]({bbox});
        way["tourism"="camp_site"]({bbox});
        node["natural"="glacier"]({bbox});
        way["natural"="cliff"]["name"]({bbox});
    """,
    "hike": """
        relation["route"="hiking"]({bbox});
        way["route"="hiking"]({bbox});
        node["natural"="peak"]({bbox});
        node["highway"="trailhead"]({bbox});
    """,
    "museum": """
        node["tourism"="museum"]({bbox});
        way["tourism"="museum"]({bbox});
        node["tourism"="gallery"]({bbox});
        way["tourism"="gallery"]({bbox});
        node["amenity"="arts_centre"]({bbox});
        way["amenity"="arts_centre"]({bbox});
    """,
    "landmark": """
        node["historic"="monument"]({bbox});
        node["historic"="memorial"]({bbox});
        node["historic"="building"]({bbox});
        way["historic"="building"]({bbox});
        node["historic"="castle"]({bbox});
        way["historic"="castle"]({bbox});
        node["historic"="ruins"]({bbox});
        node["tourism"="attraction"]({bbox});
        way["tourism"="attraction"]({bbox});
    """,
    "city": """
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

# Mapping from OSM tags to our categories — used to tag elements from a
# combined query back to the right category.
_TAG_TO_CATEGORY: list[tuple[dict[str, str], str]] = [
    # coffee
    ({"amenity": "cafe"}, "coffee"),
    ({"shop": "coffee"}, "coffee"),
    # restaurant
    ({"amenity": "restaurant"}, "restaurant"),
    ({"amenity": "bar"}, "restaurant"),
    ({"amenity": "food_court"}, "restaurant"),
    # photo
    ({"man_made": "tower"}, "photo"),
    ({"tourism": "viewpoint"}, "photo"),
    # nature
    ({"leisure": "park"}, "nature"),
    ({"leisure": "garden"}, "nature"),
    ({"leisure": "nature_reserve"}, "nature"),
    ({"waterway": "waterfall"}, "nature"),
    ({"natural": "beach"}, "nature"),
    ({"natural": "volcano"}, "nature"),
    ({"natural": "wood"}, "nature"),
    ({"tourism": "camp_site"}, "nature"),
    ({"natural": "glacier"}, "nature"),
    ({"natural": "cliff"}, "nature"),
    # hike
    ({"route": "hiking"}, "hike"),
    ({"natural": "peak"}, "hike"),
    ({"highway": "trailhead"}, "hike"),
    # museum
    ({"tourism": "museum"}, "museum"),
    ({"tourism": "gallery"}, "museum"),
    ({"amenity": "arts_centre"}, "museum"),
    # landmark
    ({"historic": "monument"}, "landmark"),
    ({"historic": "memorial"}, "landmark"),
    ({"historic": "building"}, "landmark"),
    ({"historic": "castle"}, "landmark"),
    ({"historic": "ruins"}, "landmark"),
    ({"tourism": "attraction"}, "landmark"),
    # city
    ({"tourism": "artwork"}, "city"),
    ({"place": "square"}, "city"),
]


def _classify_element(el: dict) -> str | None:
    """Determine the app category for an OSM element based on its tags."""
    tags = el.get("tags", {})
    for match_tags, category in _TAG_TO_CATEGORY:
        if all(tags.get(k) == v for k, v in match_tags.items()):
            return category
    # Fallback: pedestrian highways → city
    if tags.get("highway") == "pedestrian" and tags.get("name"):
        return "city"
    return None


async def _post_with_mirrors(
    client: httpx.AsyncClient,
    query: str,
) -> dict:
    """Try Overpass mirrors in order until one succeeds."""
    last_err = None
    for url in OVERPASS_MIRRORS:
        try:
            resp = await client.post(url, data={"data": query}, timeout=25)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning("Mirror %s failed: %s, trying next", url, e)
            last_err = e
    raise last_err or httpx.HTTPError("All Overpass mirrors failed")


async def fetch_category(
    client: httpx.AsyncClient,
    category: str,
    bbox: BBox,
) -> list[dict]:
    """Fetch POI elements from Overpass API for a single category."""
    body_clauses = CATEGORY_QUERIES[category].format(bbox=bbox.to_str())
    query = f"[out:json][timeout:25];({body_clauses});out center tags;"

    logger.info("Fetching %s for bbox %s", category, bbox.to_str())
    data = await _post_with_mirrors(client, query)
    elements = data.get("elements", [])
    logger.info("Fetched %d elements for %s", len(elements), category)
    return elements


async def fetch_all_combined(
    bbox: BBox,
    categories: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Fetch all categories in a single Overpass query (much faster).

    Combines all category clauses into one union query, then classifies
    each returned element back into its category.
    """
    to_fetch = categories if categories else list(CATEGORY_QUERIES.keys())
    # Build combined union body
    all_clauses = ""
    for cat in to_fetch:
        if cat in CATEGORY_QUERIES:
            all_clauses += CATEGORY_QUERIES[cat].format(bbox=bbox.to_str())

    query = f"[out:json][timeout:25];({all_clauses});out center tags;"
    logger.info("Fetching %d categories combined for bbox %s", len(to_fetch), bbox.to_str())

    async with httpx.AsyncClient() as client:
        data = await _post_with_mirrors(client, query)

    elements = data.get("elements", [])
    logger.info("Fetched %d elements total (combined)", len(elements))

    # Classify elements into categories
    results: dict[str, list[dict]] = {cat: [] for cat in to_fetch}
    seen_ids: set[int] = set()
    for el in elements:
        eid = el.get("id")
        if eid in seen_ids:
            continue
        seen_ids.add(eid)
        cat = _classify_element(el)
        if cat and cat in results:
            results[cat].append(el)

    for cat, els in results.items():
        logger.info("  %s: %d elements", cat, len(els))

    return results


async def fetch_all_categories(
    bbox: BBox,
    categories: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Fetch categories — uses combined query for speed, falls back to sequential."""
    try:
        return await fetch_all_combined(bbox, categories)
    except httpx.HTTPError as e:
        logger.warning("Combined fetch failed (%s), falling back to sequential", e)

    # Fallback: sequential per-category (slower but more resilient)
    to_fetch = categories if categories else list(CATEGORY_QUERIES.keys())
    results: dict[str, list[dict]] = {}
    async with httpx.AsyncClient() as client:
        for category in to_fetch:
            if category not in CATEGORY_QUERIES:
                continue
            try:
                results[category] = await fetch_category(client, category, bbox)
            except httpx.HTTPError as e:
                logger.error("Failed to fetch %s: %s", category, e)
                results[category] = []
            await asyncio.sleep(2)
    return results
