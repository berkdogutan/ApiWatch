"""Automatic scheduling with APScheduler 3.x (AsyncIOScheduler).

Jobs live in memory and are rebuilt from SQLite on every start-up, so the database is the
single source of truth. Run exactly one Uvicorn worker: each worker process would start
its own scheduler and every monitor would be checked once per worker.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.models import Monitor
from app.schemas.monitor import MAX_TIMEOUT_SECONDS
from app.services.monitoring import CheckInProgressError, MonitorChangedError, MonitorRunner

logger = logging.getLogger(__name__)

FIRST_RUN_DELAY = timedelta(seconds=2)
# New and edited monitors start within this many seconds, spread out by a golden-ratio
# sequence of their IDs. Checks that run at the same instant compete for CPU on both ends
# and inflate each other's response times, and with equal intervals they would stay
# aligned forever; the sequence keeps any number of monitors well apart.
STAGGER_WINDOW_SECONDS = 30
_GOLDEN_RATIO_FRACTION = 0.6180339887498949
# APScheduler counts running instances per job id, so a replaced job's first run would be
# skipped while the old job's check is still in flight. No check outlasts its timeout.
IN_FLIGHT_GRACE = timedelta(seconds=MAX_TIMEOUT_SECONDS + 5)


def job_id(monitor_id: int) -> str:
    return f"monitor-{monitor_id}"


def stagger_offset(monitor_id: int, interval_seconds: int) -> float:
    """Seconds to delay a monitor's first run so monitors don't fire together."""
    window = min(interval_seconds, STAGGER_WINDOW_SECONDS)
    return (monitor_id * _GOLDEN_RATIO_FRACTION) % 1 * window


class MonitorScheduler:
    def __init__(self, runner: MonitorRunner) -> None:
        self.runner = runner
        self.scheduler = AsyncIOScheduler(
            timezone=UTC,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30},
        )

    @property
    def running(self) -> bool:
        return self.scheduler.running

    def start(self) -> None:
        """Start the scheduler and recreate a job for every enabled monitor."""
        self.scheduler.start()
        with self.runner.session_factory() as db:
            monitors = db.scalars(select(Monitor).where(Monitor.enabled.is_(True))).all()
        for monitor in monitors:
            self.sync(monitor)
        logger.info("Scheduler started with %d job(s)", len(monitors))

    def shutdown(self) -> None:
        """Stop scheduling. APScheduler cancels checks that are still in flight; a
        cancelled check stores nothing, because results are written only after the
        HTTP request has finished."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def sync(self, monitor: Monitor) -> None:
        """Create, replace or remove the job so it matches the monitor's settings."""
        if not monitor.enabled:
            self.remove(monitor.id)
            return
        delay = FIRST_RUN_DELAY + timedelta(
            seconds=stagger_offset(monitor.id, monitor.interval_seconds)
        )
        if self.runner.is_running(monitor.id):
            delay += IN_FLIGHT_GRACE
        self.scheduler.add_job(
            self._run_job,
            trigger=IntervalTrigger(seconds=monitor.interval_seconds, timezone=UTC),
            id=job_id(monitor.id),
            name=monitor.name,
            args=[monitor.id],
            replace_existing=True,
            next_run_time=datetime.now(UTC) + delay,
        )

    def remove(self, monitor_id: int) -> None:
        if self.scheduler.get_job(job_id(monitor_id)) is not None:
            self.scheduler.remove_job(job_id(monitor_id))

    def job_ids(self) -> list[str]:
        return sorted(job.id for job in self.scheduler.get_jobs())

    def next_run_time(self, monitor_id: int) -> datetime | None:
        job = self.scheduler.get_job(job_id(monitor_id))
        return job.next_run_time if job is not None else None

    async def _run_job(self, monitor_id: int) -> None:
        try:
            await self.runner.check_monitor(monitor_id)
        except CheckInProgressError:
            logger.info("Skipping scheduled check for monitor %s: already running", monitor_id)
        except MonitorChangedError:
            logger.info("Discarded check of monitor %s: it was changed mid-check", monitor_id)
        except asyncio.CancelledError:
            # shutdown() cancels in-flight checks; nothing was written, so end quietly
            # instead of letting APScheduler log the cancellation as a job error.
            logger.info("Scheduled check for monitor %s cancelled by shutdown", monitor_id)
        except Exception:  # keep the scheduler alive no matter what
            logger.exception("Scheduled check for monitor %s failed", monitor_id)
