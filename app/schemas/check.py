from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UTCDateTime


class CheckResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    monitor_id: int
    checked_at: UTCDateTime
    response_time_ms: float | None
    status_code: int | None
    status: Literal["UP", "DEGRADED", "DOWN"]
    success: bool
    error_category: str | None
    error_message: str | None


class StatsRead(BaseModel):
    total_checks: int
    successful_checks: int
    failed_checks: int
    uptime_percentage: float | None
    avg_response_time_ms: float | None
    min_response_time_ms: float | None
    max_response_time_ms: float | None
    last_successful_check: UTCDateTime | None
    first_check: UTCDateTime | None


class HistoryRead(BaseModel):
    monitor_id: int
    window: Literal["24h", "7d", "all"]
    stats: StatsRead
    results: list[CheckResultRead]


class JsonChange(BaseModel):
    field: str
    change: Literal["added", "removed", "type_changed"]
    old_type: str | None
    new_type: str | None


class JsonBaselineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    monitor_id: int
    fields: dict[str, str]
    approved_at: UTCDateTime
    pending_fields: dict[str, str] | None
    pending_changes: list[JsonChange] | None
    pending_detected_at: UTCDateTime | None


class BaselineApproval(BaseModel):
    pending_fields: dict[str, str] = Field(
        description="The pending fields you reviewed, as returned by GET .../json-baseline. "
        "If a newer response has replaced them since, the approval is rejected with 409."
    )


class DashboardStats(BaseModel):
    window: Literal["24h", "7d", "all"]
    total_monitors: int
    enabled_monitors: int
    healthy: int
    degraded: int
    down: int
    pending: int
    paused: int
    open_incidents: int
    checks: StatsRead
