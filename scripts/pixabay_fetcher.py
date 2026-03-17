"""Pixabay API client for fetching place images."""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

PIXABAY_URL = "https://pixabay.com/api/"

CATEGORY_MAP = {
    "nature": "nature",
    "city": "buildings",
    "coffee": "food",
    "restaurant": "food",
    "photo": "travel",
    "hike": "nature",
    "museum": "buildings",
    "landmark": "buildings",
}


async def search_place_image(
    client: httpx.AsyncClient,
    place_name: str,
    category: str,
) -> dict | None:
    """Search Pixabay for images matching a place name."""
    settings = get_settings()
    params = {
        "key": settings.PIXABAY_API_KEY,
        "q": place_name,
        "image_type": "photo",
        "orientation": "horizontal",
        "category": CATEGORY_MAP.get(category, "travel"),
        "per_page": 5,
        "safesearch": "true",
    }

    try:
        resp = await client.get(PIXABAY_URL, params=params, timeout=10)
        resp.raise_for_status()
        hits = resp.json().get("hits", [])

        if not hits:
            return None

        return {
            "thumbnail_url": hits[0]["webformatURL"],
            "photo_urls": [h["largeImageURL"] for h in hits[:3]],
        }
    except httpx.HTTPError as e:
        logger.warning("Pixabay fetch failed for %r: %s", place_name, e)
        return None


async def fetch_images_for_places(
    places: list[dict],
) -> list[dict]:
    """Fetch images for a list of place dicts, adding thumbnail_url and photo_urls.

    Respects Pixabay rate limit: 100 req/min -> ~0.7 sec between requests.
    """
    async with httpx.AsyncClient() as client:
        for place in places:
            if place.get("thumbnail_url"):
                continue

            images = await search_place_image(
                client, place["name"], place["category"]
            )
            if images:
                place["thumbnail_url"] = images["thumbnail_url"]
                place["photo_urls"] = images["photo_urls"]

            await asyncio.sleep(0.7)

    return places
