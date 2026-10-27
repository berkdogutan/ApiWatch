from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.schemas.common import UTCDateTime


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    monitor_id: int
    monitor_name: str
    started_at: UTCDateTime
    resolved_at: UTCDateTime | None
    reason: str
    is_open: bool
    duration_seconds: float | None
