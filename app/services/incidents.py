"""Incident detection: open after N consecutive DOWN checks, close on recovery."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CheckResult, HealthStatus, Incident, Monitor


def get_open_incident(db: Session, monitor_id: int) -> Incident | None:
    return db.scalars(
        select(Incident)
        .where(Incident.monitor_id == monitor_id, Incident.resolved_at.is_(None))
        .order_by(Incident.started_at.desc())
    ).first()


def update_incidents(
    db: Session, monitor: Monitor, latest: CheckResult, failure_threshold: int
) -> Incident | None:
    """Apply incident rules after ``latest`` has been added (and flushed) to the session.

    Returns the incident that was opened or resolved, if any.
    """
    open_incident = get_open_incident(db, monitor.id)

    if latest.status != HealthStatus.DOWN:
        if open_incident is not None:
            open_incident.resolved_at = latest.checked_at
            return open_incident
        return None

    if open_incident is not None:
        return None  # already tracking this outage; never duplicate

    recent = db.scalars(
        select(CheckResult)
        .where(CheckResult.monitor_id == monitor.id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(failure_threshold)
    ).all()
    if len(recent) < failure_threshold or any(r.status != HealthStatus.DOWN for r in recent):
        return None

    first_failure = recent[-1]
    incident = Incident(
        monitor_id=monitor.id,
        started_at=first_failure.checked_at,
        reason=latest.error_message or "Monitor is DOWN",
    )
    db.add(incident)
    return incident
