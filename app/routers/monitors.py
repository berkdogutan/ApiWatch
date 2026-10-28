from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.models import CheckResult, Incident, JsonBaseline, Monitor
from app.routers.params import MonitorId
from app.schemas.check import (
    BaselineApproval,
    CheckResultRead,
    HistoryRead,
    JsonBaselineRead,
    StatsRead,
)
from app.schemas.monitor import MonitorCreate, MonitorRead, MonitorSummary, MonitorUpdate
from app.services import analytics
from app.services.json_changes import approve_pending
from app.services.monitoring import CheckInProgressError, MonitorChangedError, MonitorRunner
from app.services.url_validation import RESOLVER_POOL, UnsafeURLError, validate_url

router = APIRouter(prefix="/api/monitors", tags=["monitors"])

URL_VALIDATION_TIMEOUT_SECONDS = 10


def url_error(message: str) -> HTTPException:
    """422 shaped like FastAPI's own validation errors so clients handle both alike."""
    return HTTPException(
        status_code=422,
        detail=[{"loc": ["body", "url"], "msg": message, "type": "value_error.url"}],
    )


def ensure_safe_url(settings: Settings, url: str) -> None:
    """Validate a URL that is about to be saved. The DNS lookup runs in its own thread, so
    a hung resolver is abandoned after the timeout instead of hanging the request."""
    future = RESOLVER_POOL.submit(validate_url, url, settings)
    try:
        future.result(timeout=URL_VALIDATION_TIMEOUT_SECONDS)
    except UnsafeURLError as exc:
        raise url_error(str(exc)) from None
    except TimeoutError:
        raise url_error("Timed out resolving the URL's host name") from None


def get_monitor_or_404(db: Session, monitor_id: int) -> Monitor:
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail=f"Monitor {monitor_id} not found")
    return monitor


def build_summary(db: Session, request: Request, monitor: Monitor) -> MonitorSummary:
    latest = db.scalars(
        select(CheckResult)
        .where(CheckResult.monitor_id == monitor.id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(1)
    ).first()
    has_open = db.scalar(
        select(func.count(Incident.id)).where(
            Incident.monitor_id == monitor.id, Incident.resolved_at.is_(None)
        )
    )
    baseline = db.scalar(select(JsonBaseline).where(JsonBaseline.monitor_id == monitor.id))
    scheduler = request.app.state.scheduler
    return MonitorSummary(
        current_status=latest.status if latest else "PENDING",
        last_checked_at=latest.checked_at if latest else None,
        last_response_time_ms=latest.response_time_ms if latest else None,
        last_status_code=latest.status_code if latest else None,
        uptime_24h=analytics.compute_stats(db, "24h", monitor.id)["uptime_percentage"],
        has_open_incident=bool(has_open),
        pending_json_changes=bool(
            monitor.inspect_json and baseline and baseline.pending_fields is not None
        ),
        next_check_at=scheduler.next_run_time(monitor.id) if scheduler else None,
    )


def to_read(db: Session, request: Request, monitor: Monitor) -> MonitorRead:
    read = MonitorRead.model_validate(monitor)
    read.summary = build_summary(db, request, monitor)
    return read


def commit_unique_name(db: Session, name: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail=f"A monitor named '{name}' already exists") from None


def sync_schedule(request: Request, monitor: Monitor) -> None:
    scheduler = request.app.state.scheduler
    if scheduler is not None:
        scheduler.sync(monitor)


@router.get("", response_model=list[MonitorRead])
def list_monitors(request: Request, db: Session = Depends(get_db)) -> list[MonitorRead]:
    monitors = db.scalars(select(Monitor).order_by(Monitor.name)).all()
    return [to_read(db, request, m) for m in monitors]


@router.post("", response_model=MonitorRead, status_code=status.HTTP_201_CREATED)
def create_monitor(
    payload: MonitorCreate, request: Request, db: Session = Depends(get_db)
) -> MonitorRead:
    ensure_safe_url(request.app.state.settings, payload.url)
    monitor = Monitor(**payload.model_dump())
    db.add(monitor)
    commit_unique_name(db, payload.name)
    db.refresh(monitor)
    sync_schedule(request, monitor)
    return to_read(db, request, monitor)


@router.get("/{monitor_id}", response_model=MonitorRead)
def get_monitor(
    monitor_id: MonitorId, request: Request, db: Session = Depends(get_db)
) -> MonitorRead:
    return to_read(db, request, get_monitor_or_404(db, monitor_id))


@router.put("/{monitor_id}", response_model=MonitorRead)
def update_monitor(
    monitor_id: MonitorId, payload: MonitorUpdate, request: Request, db: Session = Depends(get_db)
) -> MonitorRead:
    monitor = get_monitor_or_404(db, monitor_id)
    if payload.url != monitor.url:
        # An unchanged URL is re-validated before every check anyway. Validating it here
        # too would make a monitor whose host stopped resolving impossible to pause or edit.
        ensure_safe_url(request.app.state.settings, payload.url)
    for field, value in payload.model_dump().items():
        setattr(monitor, field, value)
    # The JSON baseline is kept even when inspection is turned off: re-enabling it later
    # reports whatever changed meanwhile instead of silently accepting it.
    commit_unique_name(db, payload.name)
    db.refresh(monitor)
    sync_schedule(request, monitor)
    return to_read(db, request, monitor)


@router.delete("/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_monitor(
    monitor_id: MonitorId, request: Request, db: Session = Depends(get_db)
) -> Response:
    monitor = get_monitor_or_404(db, monitor_id)
    db.delete(monitor)
    db.commit()
    scheduler = request.app.state.scheduler
    if scheduler is not None:
        scheduler.remove(monitor_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{monitor_id}/check", response_model=CheckResultRead)
async def run_manual_check(monitor_id: MonitorId, request: Request) -> CheckResult:
    runner: MonitorRunner = request.app.state.runner
    try:
        result = await runner.check_monitor(monitor_id)
    except CheckInProgressError as exc:
        raise HTTPException(409, detail=str(exc)) from None
    except MonitorChangedError:
        raise HTTPException(
            409, detail="The monitor was changed while the check was running; run it again"
        ) from None
    if result is None:
        raise HTTPException(404, detail=f"Monitor {monitor_id} not found")
    return result


@router.get("/{monitor_id}/history", response_model=HistoryRead)
def get_history(
    monitor_id: MonitorId,
    window: Literal["24h", "7d", "all"] = "24h",
    limit: int = Query(default=500, ge=1, le=5000),
    points: int | None = Query(
        default=None,
        ge=10,
        le=2000,
        description="Instead of the newest `limit` results, return about this many results "
        "spread evenly over the whole window, plus the first and last DOWN check of each "
        "outage. Useful for charts.",
    ),
    db: Session = Depends(get_db),
) -> HistoryRead:
    get_monitor_or_404(db, monitor_id)
    if points is not None:
        results = analytics.sampled_results(db, window, monitor_id, points)
    else:
        results = analytics.recent_results(db, window, monitor_id, limit)
    return HistoryRead(
        monitor_id=monitor_id,
        window=window,
        stats=StatsRead(**analytics.compute_stats(db, window, monitor_id)),
        results=[CheckResultRead.model_validate(r) for r in results],
    )


@router.get("/{monitor_id}/json-baseline", response_model=JsonBaselineRead)
def get_json_baseline(monitor_id: MonitorId, db: Session = Depends(get_db)) -> JsonBaseline:
    monitor = get_monitor_or_404(db, monitor_id)
    if monitor.json_baseline is None:
        raise HTTPException(404, detail="No JSON baseline captured yet")
    return monitor.json_baseline


@router.post("/{monitor_id}/json-baseline/approve", response_model=JsonBaselineRead)
def approve_json_baseline(
    monitor_id: MonitorId, payload: BaselineApproval, db: Session = Depends(get_db)
) -> JsonBaseline:
    monitor = get_monitor_or_404(db, monitor_id)
    baseline = monitor.json_baseline
    if baseline is None or baseline.pending_fields is None:
        raise HTTPException(409, detail="There are no pending JSON changes to approve")
    if baseline.pending_fields != payload.pending_fields:
        raise HTTPException(
            409,
            detail="The pending JSON changes were replaced by a newer response; "
            "review them again before approving",
        )
    approve_pending(baseline)
    db.commit()
    db.refresh(baseline)
    return baseline
