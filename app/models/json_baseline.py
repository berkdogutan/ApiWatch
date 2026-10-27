from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.monitor import utcnow

if TYPE_CHECKING:
    from app.models.monitor import Monitor


class JsonBaseline(Base):
    """Approved top-level key -> JSON type map for a monitor, plus any unapproved drift.

    Only key names and type names are stored - never response values.
    """

    __tablename__ = "json_baselines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    monitor_id: Mapped[int] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE"), unique=True
    )
    fields: Mapped[dict[str, str]] = mapped_column(JSON)
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    pending_fields: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    pending_changes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    pending_detected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    monitor: Mapped[Monitor] = relationship(back_populates="json_baseline")
