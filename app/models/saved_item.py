import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SavedPlace(Base):
    __tablename__ = "saved_places"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[str] = mapped_column(String(255), nullable=False)
    place_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    place = relationship("Place")

    __table_args__ = (
        UniqueConstraint(
            "device_id", "place_id", name="uq_saved_places_device_place"
        ),
        Index("ix_saved_places_device_id", "device_id"),
    )

    def __repr__(self) -> str:
        return f"<SavedPlace device={self.device_id!r} place={self.place_id}>"


class SavedAdventure(Base):
    __tablename__ = "saved_adventures"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[str] = mapped_column(String(255), nullable=False)
    adventure_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("adventures.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    adventure = relationship("Adventure")

    __table_args__ = (
        UniqueConstraint(
            "device_id", "adventure_id", name="uq_saved_adventures_device_adventure"
        ),
        Index("ix_saved_adventures_device_id", "device_id"),
    )

    def __repr__(self) -> str:
        return f"<SavedAdventure device={self.device_id!r} adventure={self.adventure_id}>"
