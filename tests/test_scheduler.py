from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import CheckResult, Monitor
from app.services import scheduler as scheduler_module
from app.services.checker import build_http_client
from app.services.monitoring import CheckInProgressError, MonitorRunner
from app.services.scheduler import MonitorScheduler, job_id
from tests.conftest import MockAPI, monitor_payload


def add_monitor(factory, **overrides) -> Monitor:
    data = monitor_payload(**overrides)
    with factory() as db:
        monitor = Monitor(**data)
        db.add(monitor)
        db.commit()
        db.refresh(monitor)
        return monitor


async def test_scheduler_lifecycle(session_factory, settings: Settings, mock_api: MockAPI) -> None:
    enabled = add_monitor(session_factory, name="Enabled")
    disabled = add_monitor(session_factory, name="Disabled", enabled=False)

    async with build_http_client(httpx.MockTransport(mock_api)) as client:
        scheduler = MonitorScheduler(MonitorRunner(session_factory, client, settings))
        scheduler.start()  # recreates jobs from the database
        try:
            assert scheduler.running
            assert scheduler.job_ids() == [job_id(enabled.id)]

            # Interval changes replace the job.
            enabled.interval_seconds = 120
            scheduler.sync(enabled)
            job = scheduler.scheduler.get_job(job_id(enabled.id))
            assert job.trigger.interval.total_seconds() == 120
            assert job.max_instances == 1

            # Enabling adds a job, disabling removes it.
            disabled.enabled = True
            scheduler.sync(disabled)
            assert len(scheduler.job_ids()) == 2
            enabled.enabled = False
            scheduler.sync(enabled)
            assert scheduler.job_ids() == [job_id(disabled.id)]

            # Monitors are staggered so they don't all fire at the same instant.
            enabled.enabled = True
            scheduler.sync(enabled)
            gap = scheduler.next_run_time(disabled.id) - scheduler.next_run_time(enabled.id)
            assert abs(gap.total_seconds() - (disabled.id - enabled.id)) < 0.5

            scheduler.remove(disabled.id)
            scheduler.remove(disabled.id)  # idempotent
            assert [job_id(enabled.id)] == scheduler.job_ids()
            scheduler.remove(enabled.id)
            assert scheduler.job_ids() == []
        finally:
            scheduler.shutdown()
        await asyncio.sleep(0)
        assert not scheduler.running


async def test_scheduled_job_runs_check(session_factory, settings: Settings, mock_api) -> None:
    monitor = add_monitor(session_factory)
    async with build_http_client(httpx.MockTransport(mock_api)) as client:
        scheduler = MonitorScheduler(MonitorRunner(session_factory, client, settings))
        await scheduler._run_job(monitor.id)  # what APScheduler invokes on each tick
    with session_factory() as db:
        assert db.query(CheckResult).filter_by(monitor_id=monitor.id).count() == 1


async def test_overlapping_checks_are_prevented(session_factory, settings: Settings) -> None:
    monitor = add_monitor(session_factory)
    release = asyncio.Event()

    async def blocking(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200)

    async with build_http_client(httpx.MockTransport(blocking)) as client:
        runner = MonitorRunner(session_factory, client, settings)
        first = asyncio.create_task(runner.check_monitor(monitor.id))
        await asyncio.sleep(0.05)
        assert runner.is_running(monitor.id)
        with pytest.raises(CheckInProgressError):
            await runner.check_monitor(monitor.id)
        # The scheduler swallows the overlap instead of queueing a second check.
        await MonitorScheduler(runner)._run_job(monitor.id)
        release.set()
        assert (await first).status == "UP"
    with session_factory() as db:
        assert db.query(CheckResult).count() == 1


async def test_shutdown_cancels_in_flight_checks_cleanly(
    session_factory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(scheduler_module, "FIRST_RUN_DELAY", timedelta(0))
    monkeypatch.setattr(scheduler_module, "STAGGER_SECONDS", 1)  # run immediately
    add_monitor(session_factory)
    started = asyncio.Event()

    async def hanging(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(30)
        return httpx.Response(200)

    async with build_http_client(httpx.MockTransport(hanging)) as client:
        scheduler = MonitorScheduler(MonitorRunner(session_factory, client, settings))
        scheduler.start()
        await asyncio.wait_for(started.wait(), timeout=5)  # APScheduler ran the job
        scheduler.shutdown()
        await asyncio.sleep(0.1)
        assert not scheduler.running

    with session_factory() as db:
        assert db.query(CheckResult).count() == 0  # nothing half-written
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_app_keeps_jobs_in_sync(tmp_path, transport) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'sched.db'}",
        scheduler_enabled=True,
        trusted_hosts=["testserver"],
    )
    with TestClient(create_app(settings, transport=transport)) as client:
        scheduler = client.app.state.scheduler
        assert client.get("/health").json()["scheduler_running"] is True

        monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
        assert scheduler.job_ids() == [job_id(monitor_id)]
        assert client.get(f"/api/monitors/{monitor_id}").json()["summary"]["next_check_at"]

        client.put(f"/api/monitors/{monitor_id}", json=monitor_payload(enabled=False))
        assert scheduler.job_ids() == []

        client.put(f"/api/monitors/{monitor_id}", json=monitor_payload(interval_seconds=90))
        assert scheduler.job_ids() == [job_id(monitor_id)]

        client.delete(f"/api/monitors/{monitor_id}")
        assert scheduler.job_ids() == []

        client.post("/api/monitors", json=monitor_payload(name="Persisted"))

    # Restart: jobs are recreated from SQLite.
    with TestClient(create_app(settings, transport=transport)) as client:
        assert len(client.app.state.scheduler.job_ids()) == 1
    assert not client.app.state.scheduler.running
