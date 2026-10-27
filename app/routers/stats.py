from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CheckResult, Incident, Monitor
from app.schemas.check import CheckResultRead, DashboardStats, StatsRead
from app.schemas.incident import IncidentRead
from app.services import analytics

router = APIRouter(tags=["stats"])


@router.get("/api/stats", response_model=DashboardStats)
def get_stats(
    window: Literal["24h", "7d", "all"] = "24h", db: Session = Depends(get_db)
) -> DashboardStats:
    """Fleet-wide summary. Status counts use each enabled monitor's latest check."""
    monitors = db.scalars(select(Monitor)).all()
    counts = {"UP": 0, "DEGRADED": 0, "DOWN": 0, "PENDING": 0}
    paused = 0
    for monitor in monitors:
        if not monitor.enabled:
            paused += 1
            continue
        latest = db.scalar(
            select(CheckResult.status)
            .where(CheckResult.monitor_id == monitor.id)
            .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
            .limit(1)
        )
        counts[latest or "PENDING"] += 1
    open_incidents = db.scalar(
        select(func.count(Incident.id)).where(Incident.resolved_at.is_(None))
    )
    return DashboardStats(
        window=window,
        total_monitors=len(monitors),
        enabled_monitors=len(monitors) - paused,
        healthy=counts["UP"],
        degraded=counts["DEGRADED"],
        down=counts["DOWN"],
        pending=counts["PENDING"],
        paused=paused,
        open_incidents=open_incidents or 0,
        checks=StatsRead(**analytics.compute_stats(db, window)),
    )


@router.get("/api/incidents", response_model=list[IncidentRead])
def list_incidents(
    status: Literal["open", "resolved", "all"] = "all",
    monitor_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[IncidentRead]:
    query = (
        select(Incident, Monitor.name)
        .join(Monitor, Monitor.id == Incident.monitor_id)
        .order_by(Incident.resolved_at.is_not(None), Incident.started_at.desc())
        .limit(limit)
    )
    if status == "open":
        query = query.where(Incident.resolved_at.is_(None))
    elif status == "resolved":
        query = query.where(Incident.resolved_at.is_not(None))
    if monitor_id is not None:
        query = query.where(Incident.monitor_id == monitor_id)
    return [
        IncidentRead(
            id=incident.id,
            monitor_id=incident.monitor_id,
            monitor_name=name,
            started_at=incident.started_at,
            resolved_at=incident.resolved_at,
            reason=incident.reason,
            is_open=incident.is_open,
            duration_seconds=(
                (incident.resolved_at - incident.started_at).total_seconds()
                if incident.resolved_at
                else None
            ),
        )
        for incident, name in db.execute(query).all()
    ]


@router.get("/api/failures", response_model=list[CheckResultRead])
def recent_failures(
    monitor_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[CheckResult]:
    """Most recent failed (DOWN) checks with their error details."""
    query = (
        select(CheckResult)
        .where(CheckResult.success.is_(False))
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(limit)
    )
    if monitor_id is not None:
        query = query.where(CheckResult.monitor_id == monitor_id)
    return list(db.scalars(query).all())


@router.get("/health", tags=["health"])
def health(request: Request, db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    scheduler = request.app.state.scheduler
    return {
        "status": "ok",
        "database": "ok",
        "scheduler_running": bool(scheduler and scheduler.running),
        "scheduled_jobs": len(scheduler.job_ids()) if scheduler else 0,
    }
