import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Place(Base):
    __tablename__ = "places"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    osm_id: Mapped[str] = mapped_column(String, nullable=False)
    osm_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    geog = mapped_column(
        Geography("POINT", srid=4326), nullable=False
    )
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_urls = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    tags = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    best_time_to_visit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    working_hours: Mapped[str | None] = mapped_column(String(255), nullable=True)
    images_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("osm_id", "osm_type", name="uq_places_osm_id_osm_type"),
        Index(
            "ix_places_geog_active",
            "geog",
            postgresql_using="gist",
            postgresql_where=(is_active.is_(True)),
        ),
        Index(
            "ix_places_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index("ix_places_category", "category"),
        Index("ix_places_rating_desc", rating.desc()),
    )

    def __repr__(self) -> str:
        return f"<Place {self.name!r} ({self.category})>"
