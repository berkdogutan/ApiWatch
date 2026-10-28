"""Shared request parameters."""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query

# SQLite integers are 64-bit; larger ids would fail inside the driver with a 500.
MAX_ID = 2**63 - 1

MonitorId = Annotated[int, Path(ge=1, le=MAX_ID)]
MonitorFilter = Annotated[int | None, Query(ge=1, le=MAX_ID)]
