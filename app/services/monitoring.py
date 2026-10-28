"""Run a check for one monitor and persist its result, incidents and JSON drift."""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import CheckResult, Monitor, utcnow
from app.services.checker import MAX_CONCURRENT_CHECKS, CheckOutcome, run_check
from app.services.incidents import update_incidents
from app.services.json_changes import record_observation

logger = logging.getLogger(__name__)


class CheckInProgressError(RuntimeError):
    """A check for this monitor is already running."""


class MonitorChangedError(RuntimeError):
    """The monitor was edited, or deleted and replaced, while its check was running."""


def _row_values(monitor: Monitor) -> tuple[object, ...]:
    # Compared instead of updated_at alone, which a coarse clock (15.6 ms ticks on Windows
    # with Python 3.12) can leave unchanged when an edit closely follows the previous write.
    return tuple(getattr(monitor, column.key) for column in inspect(Monitor).column_attrs)


class MonitorRunner:
    """Executes checks, guaranteeing at most one in-flight check per monitor."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        client: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.client = client
        self.settings = settings
        self._locks: dict[int, asyncio.Lock] = {}
        # Checks wait here rather than in the HTTP connection pool, so queueing time is
        # never counted as response time or towards the check's timeout.
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)

    def is_running(self, monitor_id: int) -> bool:
        lock = self._locks.get(monitor_id)
        return lock is not None and lock.locked()

    async def check_monitor(self, monitor_id: int) -> CheckResult | None:
        """Check a monitor now. Returns ``None`` if the monitor no longer exists.

        Raises :class:`CheckInProgressError` instead of overlapping a running check, and
        :class:`MonitorChangedError` (storing nothing) if the monitor's configuration
        changed while the request was in flight.

        Database work runs in worker threads: a SQLite lock wait must never stall the
        event loop, where it would inflate the timing of every other check in flight.
        """
        lock = self._locks.setdefault(monitor_id, asyncio.Lock())
        if lock.locked():
            raise CheckInProgressError(f"A check for monitor {monitor_id} is already running")
        async with lock:
            monitor = await asyncio.to_thread(self._load, monitor_id)
            if monitor is None:
                return None
            async with self._slots:
                outcome = await run_check(monitor, self.client, self.settings)
            return await asyncio.to_thread(self._store, monitor, outcome)

    def _load(self, monitor_id: int) -> Monitor | None:
        with self.session_factory() as db:
            return db.get(Monitor, monitor_id)

    def _store(self, checked: Monitor, outcome: CheckOutcome) -> CheckResult | None:
        with self.session_factory() as db:
            monitor = db.get(Monitor, checked.id)
            if monitor is None:  # deleted while the request was in flight
                return None
            if _row_values(monitor) != _row_values(checked):
                raise MonitorChangedError(
                    f"Monitor {checked.id} was changed while its check was running"
                )
            result = CheckResult(
                monitor_id=monitor.id,
                checked_at=utcnow(),
                response_time_ms=outcome.response_time_ms,
                status_code=outcome.status_code,
                status=outcome.status.value,
                success=outcome.success,
                error_category=outcome.error_category,
                error_message=outcome.error_message,
            )
            db.add(result)
            db.flush()
            update_incidents(db, monitor, result, self.settings.incident_failure_threshold)
            if monitor.inspect_json and outcome.json_fields is not None:
                record_observation(db, monitor, outcome.json_fields)
            db.commit()
            logger.info(
                "Checked %s: %s (%s ms)", monitor.name, result.status, result.response_time_ms
            )
            return result
