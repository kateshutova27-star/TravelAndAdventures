import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Adventure(Base):
    __tablename__ = "adventures"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    created_by_device_id: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    generation_params = mapped_column(JSONB, nullable=True)
    total_time_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    drive_time_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    encoded_polyline: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    stops: Mapped[list["AdventureStop"]] = relationship(
        back_populates="adventure",
        cascade="all, delete-orphan",
        order_by="AdventureStop.order",
    )

    def __repr__(self) -> str:
        return f"<Adventure {self.id} status={self.status!r}>"


class AdventureStop(Base):
    __tablename__ = "adventure_stops"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    adventure_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("adventures.id", ondelete="CASCADE"), nullable=False
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("places.id"), nullable=False
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    drive_time_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    time_to_spend_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )

    adventure: Mapped["Adventure"] = relationship(back_populates="stops")
    place = relationship("Place", lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "adventure_id", "order", name="uq_adventure_stops_adventure_order"
        ),
    )

    def __repr__(self) -> str:
        return f"<AdventureStop adventure={self.adventure_id} order={self.order}>"


class ActiveRouteSession(Base):
    __tablename__ = "active_route_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    adventure_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("adventures.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    current_stop_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="in_progress", server_default="in_progress"
    )
    completed_stops = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    last_known_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_known_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    adventure: Mapped["Adventure"] = relationship()

    def __repr__(self) -> str:
        return f"<ActiveRouteSession {self.id} status={self.status!r}>"
