from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import UTCDateTime

MIN_INTERVAL_SECONDS = 30
MAX_INTERVAL_SECONDS = 3600

# Field constraints shared by create (with defaults) and update (all required).
Name = Annotated[str, Field(min_length=1, max_length=100, examples=["Payments API"])]
URL = Annotated[
    str, Field(min_length=1, max_length=2048, examples=["https://api.example.com/health"])
]
Method = Literal["GET", "HEAD"]
ExpectedStatus = Annotated[int, Field(ge=100, le=599)]
IntervalSeconds = Annotated[int, Field(ge=MIN_INTERVAL_SECONDS, le=MAX_INTERVAL_SECONDS)]
TimeoutSeconds = Annotated[float, Field(gt=0, le=30)]
SlowThresholdMs = Annotated[int, Field(ge=1, le=60_000)]
InspectJson = Annotated[bool, Field(description="Track top-level JSON keys/types (GET only).")]


class MonitorFields(BaseModel):
    """Validation shared by every monitor payload."""

    name: str
    url: str
    method: Method
    expected_status: int
    interval_seconds: int
    timeout_seconds: float
    slow_threshold_ms: int
    enabled: bool
    inspect_json: bool

    @field_validator("name", "url")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("method", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _timeout_below_interval(self) -> MonitorFields:
        if self.timeout_seconds >= self.interval_seconds:
            raise ValueError("timeout_seconds must be shorter than interval_seconds")
        return self


class MonitorCreate(MonitorFields):
    """``POST /api/monitors``: only ``name`` and ``url`` are required."""

    name: Name
    url: URL
    method: Method = "GET"
    expected_status: ExpectedStatus = 200
    interval_seconds: IntervalSeconds = 60
    timeout_seconds: TimeoutSeconds = 10.0
    slow_threshold_ms: SlowThresholdMs = 1000
    enabled: bool = True
    inspect_json: InspectJson = False


class MonitorUpdate(MonitorFields):
    """``PUT /api/monitors/{id}`` replaces the whole configuration, so every field is
    required; an omitted field is an error rather than a silent reset to its default."""

    name: Name
    url: URL
    method: Method
    expected_status: ExpectedStatus
    interval_seconds: IntervalSeconds
    timeout_seconds: TimeoutSeconds
    slow_threshold_ms: SlowThresholdMs
    enabled: bool
    inspect_json: InspectJson


class MonitorSummary(BaseModel):
    current_status: Literal["UP", "DEGRADED", "DOWN", "PENDING"]
    last_checked_at: UTCDateTime | None = None
    last_response_time_ms: float | None = None
    last_status_code: int | None = None
    uptime_24h: float | None = None
    has_open_incident: bool = False
    pending_json_changes: bool = False
    next_check_at: UTCDateTime | None = None


class MonitorRead(MonitorCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: UTCDateTime
    updated_at: UTCDateTime
    summary: MonitorSummary | None = None
