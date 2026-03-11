"""POI import orchestrator: OSM fetch -> enrich -> upsert -> Pixabay images."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone

from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.database import async_session_factory
from app.models.place import Place
from scripts.enrichment import transform_element
from scripts.osm_fetcher import BBox, fetch_all_categories
from scripts.pixabay_fetcher import fetch_images_for_places

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Predefined regions for import
REGIONS = {
    "calgary": BBox(south=50.84, west=-114.31, north=51.21, east=-113.86),
    "vancouver": BBox(south=49.20, west=-123.27, north=49.32, east=-123.02),
    "paris": BBox(south=48.81, west=2.22, north=48.90, east=2.47),
    "barcelona": BBox(south=41.32, west=2.07, north=41.47, east=2.23),
}


async def upsert_places(places: list[dict]) -> int:
    """Upsert place dicts into database. Returns count of upserted rows."""
    if not places:
        return 0

    count = 0
    async with async_session_factory() as session:
        for place in places:
            stmt = pg_insert(Place).values(
                osm_id=place["osm_id"],
                osm_type=place["osm_type"],
                name=place["name"],
                category=place["category"],
                lat=place["lat"],
                lng=place["lng"],
                geog=ST_SetSRID(ST_MakePoint(place["lng"], place["lat"]), 4326),
                description=place.get("description", ""),
                tags=place.get("tags", []),
                working_hours=place.get("working_hours"),
                best_time_to_visit=place.get("best_time_to_visit"),
                rating=place.get("rating"),
                review_count=place.get("review_count", 0),
                thumbnail_url=place.get("thumbnail_url"),
                photo_urls=place.get("photo_urls", []),
                images_fetched_at=datetime.now(timezone.utc) if place.get("thumbnail_url") else None,
            ).on_conflict_do_update(
                constraint="uq_places_osm_id_osm_type",
                set_={
                    "name": place["name"],
                    "description": place.get("description", ""),
                    "tags": place.get("tags", []),
                    "working_hours": place.get("working_hours"),
                    "thumbnail_url": place.get("thumbnail_url"),
                    "photo_urls": place.get("photo_urls", []),
                    "updated_at": func.now(),
                },
            )
            await session.execute(stmt)
            count += 1

        await session.commit()

    return count


async def import_region(region_name: str, bbox: BBox, fetch_images: bool = True) -> None:
    """Import all POI categories for a region."""
    logger.info("Starting import for region: %s", region_name)

    # Step 1: Fetch from OSM
    raw_data = await fetch_all_categories(bbox)

    # Step 2: Transform and enrich
    all_places: list[dict] = []
    for category, elements in raw_data.items():
        for el in elements:
            place = transform_element(el, category)
            if place:
                all_places.append(place)

    logger.info("Transformed %d places for %s", len(all_places), region_name)

    # Step 3: Fetch images from Pixabay (optional)
    settings = get_settings()
    if fetch_images and settings.PIXABAY_API_KEY:
        logger.info("Fetching images from Pixabay...")
        all_places = await fetch_images_for_places(all_places)
        with_images = sum(1 for p in all_places if p.get("thumbnail_url"))
        logger.info("Got images for %d / %d places", with_images, len(all_places))

    # Step 4: Upsert to database
    count = await upsert_places(all_places)
    logger.info("Upserted %d places for %s", count, region_name)


async def main() -> None:
    """CLI entry point: python -m scripts.import_pois [region_name]"""
    region_name = sys.argv[1] if len(sys.argv) > 1 else "calgary"
    no_images = "--no-images" in sys.argv

    if region_name == "all":
        for name, bbox in REGIONS.items():
            await import_region(name, bbox, fetch_images=not no_images)
    elif region_name in REGIONS:
        await import_region(region_name, REGIONS[region_name], fetch_images=not no_images)
    else:
        logger.error("Unknown region: %s. Available: %s", region_name, list(REGIONS.keys()))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
