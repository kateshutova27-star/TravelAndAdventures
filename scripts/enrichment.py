"""Transform and enrich raw OSM elements into Place-compatible dicts."""

from __future__ import annotations

import hashlib
import re

import bleach


def transform_element(el: dict, category: str) -> dict | None:
    """Transform a raw OSM element into a Place-compatible dictionary.

    Returns None if the element lacks a name (skip unnamed POIs).
    """
    tags = el.get("tags", {})
    name = tags.get("name:en") or tags.get("name")
    if not name:
        return None

    # Coordinates: nodes have lat/lon, ways/relations use "center"
    lat = el.get("lat") or (el.get("center", {}) or {}).get("lat")
    lng = el.get("lon") or (el.get("center", {}) or {}).get("lon")
    if lat is None or lng is None:
        return None

    # Sanitize text fields
    name = _sanitize(name, max_length=255)
    description = _sanitize(
        tags.get("description:en") or tags.get("description") or tags.get("note") or "",
        max_length=2000,
    )

    working_hours = tags.get("opening_hours")
    if working_hours:
        working_hours = _sanitize(working_hours, max_length=255)

    app_tags = _extract_tags(tags, category)
    best_time = _infer_best_time(category)
    rating, review_count = _generate_mock_rating(str(el["id"]))

    return {
        "osm_id": str(el["id"]),
        "osm_type": el["type"],
        "name": name,
        "category": category,
        "lat": float(lat),
        "lng": float(lng),
        "description": description,
        "tags": app_tags,
        "working_hours": working_hours,
        "best_time_to_visit": best_time,
        "rating": rating,
        "review_count": review_count,
        "thumbnail_url": None,
        "photo_urls": [],
    }


def _sanitize(text: str, max_length: int = 255) -> str:
    """Remove HTML tags, control characters, and truncate."""
    text = bleach.clean(text, tags=[], strip=True)
    # Remove control unicode characters
    text = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069\u200b]", "", text)
    return text[:max_length].strip()


def _extract_tags(tags: dict, category: str) -> list[str]:
    """Extract meaningful app tags from OSM tags."""
    result = [category]

    tag_extractors = {
        "cuisine": lambda v: v.split(";"),
        "diet:vegan": lambda v: ["vegan"] if v == "yes" else [],
        "diet:vegetarian": lambda v: ["vegetarian"] if v == "yes" else [],
        "outdoor_seating": lambda v: ["outdoor seating"] if v == "yes" else [],
        "wheelchair": lambda v: ["wheelchair accessible"] if v == "yes" else [],
        "internet_access": lambda v: ["wifi"] if v in ("wlan", "yes") else [],
        "dog": lambda v: ["dog friendly"] if v == "yes" else [],
        "historic": lambda v: [v],
        "natural": lambda v: [v],
        "leisure": lambda v: [v],
        "fee": lambda v: ["free entry"] if v == "no" else ["paid entry"] if v == "yes" else [],
    }

    for key, extractor in tag_extractors.items():
        if key in tags:
            result.extend(extractor(tags[key]))

    return list(set(result))


def _infer_best_time(category: str) -> str | None:
    mapping = {
        "nature": "morning",
        "photo": "golden hour (sunrise/sunset)",
        "city": "morning or late afternoon",
        "coffee": "morning",
        "restaurant": "evening",
        "hike": "early morning",
        "museum": "morning or early afternoon",
        "landmark": "morning or late afternoon",
    }
    return mapping.get(category)


def _generate_mock_rating(osm_id: str) -> tuple[float, int]:
    """Deterministic pseudo-random rating based on OSM ID."""
    h = int(hashlib.md5(osm_id.encode()).hexdigest(), 16)
    rating = round(3.5 + (h % 1000) / 1000.0 * 1.4, 1)
    review_count = int(5 + (h % 10000) / 10000.0 * 495)
    return rating, review_count
