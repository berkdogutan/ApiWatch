"""Metrics computed strictly from stored check results (aggregated in SQL)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, Subquery, case, func, or_, select, union
from sqlalchemy.orm import Session

from app.models import CheckResult, Monitor, utcnow

Window = Literal["24h", "7d", "all"]
WINDOWS: dict[str, timedelta | None] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "all": None,
}


def window_start(window: Window) -> datetime | None:
    delta = WINDOWS[window]
    return utcnow() - delta if delta is not None else None


def uptime_percentage(successful: int, total: int) -> float | None:
    """Uptime = successful / total * 100. ``None`` (shown as N/A) when there is no data."""
    if total == 0:
        return None
    return round(successful / total * 100, 2)


def _filters(window: Window, monitor_id: int | None) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    if monitor_id is not None:
        filters.append(CheckResult.monitor_id == monitor_id)
    else:
        # Always true (results are deleted with their monitor), but lets SQLite use the
        # (monitor_id, checked_at) index instead of scanning the whole history.
        filters.append(CheckResult.monitor_id.in_(select(Monitor.id)))
    start = window_start(window)
    if start is not None:
        filters.append(CheckResult.checked_at >= start)
    return filters


def compute_stats(db: Session, window: Window, monitor_id: int | None = None) -> dict[str, Any]:
    """Aggregate the checks in ``window``. UP and DEGRADED both count as successful.

    Response-time statistics use only checks that received an HTTP response, so
    timeouts and connection failures don't distort latency numbers.
    """
    succeeded = CheckResult.success.is_(True)
    timing = case((CheckResult.status_code.is_not(None), CheckResult.response_time_ms))
    total, successful, avg_ms, min_ms, max_ms, last_success, first_check = db.execute(
        select(
            func.count(CheckResult.id),
            func.sum(case((succeeded, 1), else_=0)),
            func.avg(timing),
            func.min(timing),
            func.max(timing),
            func.max(case((succeeded, CheckResult.checked_at))),
            func.min(CheckResult.checked_at),
        ).where(*_filters(window, monitor_id))
    ).one()
    successful = int(successful or 0)
    return {
        "total_checks": total,
        "successful_checks": successful,
        "failed_checks": total - successful,
        "uptime_percentage": uptime_percentage(successful, total),
        "avg_response_time_ms": round(avg_ms, 2) if avg_ms is not None else None,
        "min_response_time_ms": min_ms,
        "max_response_time_ms": max_ms,
        "last_successful_check": last_success,
        "first_check": first_check,
    }


def recent_results(
    db: Session, window: Window, monitor_id: int | None, limit: int
) -> list[CheckResult]:
    """The newest ``limit`` results in the window, newest first."""
    query = (
        select(CheckResult)
        .where(*_filters(window, monitor_id))
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(limit)
    )
    return list(db.scalars(query).all())


def _every_nth(rows: Subquery, points: int) -> Select:
    """Ids of about ``points`` rows spread evenly over ``rows`` in time order, always
    including the newest one."""
    numbered = select(
        rows.c.id,
        func.row_number().over(order_by=(rows.c.checked_at, rows.c.id)).label("rn"),
        func.count().over().label("total"),
    ).subquery()
    stride = (numbered.c.total + points - 1) // points  # ceil(total / points)
    return select(numbered.c.id).where(
        or_((numbered.c.rn - 1) % stride == 0, numbered.c.rn == numbered.c.total)
    )


def sampled_results(
    db: Session, window: Window, monitor_id: int | None, points: int
) -> list[CheckResult]:
    """About ``points`` results spread evenly over the window for charting, newest first.

    Outages are sampled separately: the first and last DOWN check of every run of failures
    (up to about ``points`` of them), so even a single failed check stays visible however
    long the window is.
    """
    order = (CheckResult.checked_at, CheckResult.id)
    rows = (
        select(
            CheckResult.id,
            CheckResult.checked_at,
            CheckResult.success,
            func.lag(CheckResult.success).over(order_by=order).label("prev_success"),
            func.lead(CheckResult.success).over(order_by=order).label("next_success"),
        )
        .where(*_filters(window, monitor_id))
        .subquery()
    )
    outage_edges = (
        select(rows.c.id, rows.c.checked_at)
        .where(
            rows.c.success.is_(False),
            or_(rows.c.prev_success.is_not(False), rows.c.next_success.is_not(False)),
        )
        .subquery()
    )
    ids = union(_every_nth(rows, points), _every_nth(outage_edges, points))
    query = (
        select(CheckResult)
        .where(CheckResult.id.in_(ids))
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
    )
    return list(db.scalars(query).all())
