from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.models import CheckResult, Monitor, utcnow
from app.services.analytics import compute_stats, sampled_results, uptime_percentage
from tests.conftest import monitor_payload


def result(
    status: str,
    ms: float | None = 100.0,
    code: int | None = 200,
    age_hours: float = 0,
    monitor_id: int = 1,
) -> CheckResult:
    return CheckResult(
        monitor_id=monitor_id,
        checked_at=utcnow() - timedelta(hours=age_hours),
        status=status,
        success=status != "DOWN",
        response_time_ms=ms,
        status_code=code,
    )


def store(session_factory, rows: list[CheckResult]) -> None:
    with session_factory() as db:
        if db.get(Monitor, 1) is None:
            db.add(Monitor(id=1, **monitor_payload()))
        db.add_all(rows)
        db.commit()


def test_uptime_percentage_formula() -> None:
    assert uptime_percentage(3, 4) == 75.0
    assert uptime_percentage(2, 3) == 66.67
    assert uptime_percentage(0, 5) == 0.0


def test_uptime_is_none_without_data(session_factory) -> None:
    assert uptime_percentage(0, 0) is None
    with session_factory() as db:
        stats = compute_stats(db, "all")
    assert stats["total_checks"] == 0
    assert stats["uptime_percentage"] is None
    assert stats["avg_response_time_ms"] is None
    assert stats["last_successful_check"] is None


def test_degraded_counts_as_successful(session_factory) -> None:
    rows = [
        result("UP", age_hours=0.3),
        result("DEGRADED", 1500, age_hours=0.2),
        result("DOWN", None, None, age_hours=0.1),  # timeout: no HTTP response
        result("DOWN", 30, 500),
    ]
    store(session_factory, rows)
    with session_factory() as db:
        stats = compute_stats(db, "24h", monitor_id=1)
    assert stats["total_checks"] == 4
    assert stats["successful_checks"] == 2
    assert stats["failed_checks"] == 2
    assert stats["uptime_percentage"] == 50.0
    # Latency stats only use checks that received an HTTP response.
    assert stats["min_response_time_ms"] == 30
    assert stats["max_response_time_ms"] == 1500
    assert stats["avg_response_time_ms"] == round((100 + 1500 + 30) / 3, 2)
    # The newest successful check is the DEGRADED one, not the later DOWN checks.
    assert stats["last_successful_check"] == rows[1].checked_at
    assert stats["first_check"] == rows[0].checked_at


def test_sampling_spreads_points_and_keeps_failures(session_factory) -> None:
    rows = [result("UP", age_hours=(100 - i) / 60) for i in range(100)]
    rows[37] = result("DOWN", None, None, age_hours=(100 - 37) / 60)
    store(session_factory, rows)
    with session_factory() as db:
        newest = db.query(CheckResult).order_by(CheckResult.checked_at.desc()).first()
        sampled = sampled_results(db, "all", 1, points=10)
    assert len(sampled) <= 12
    assert any(r.status == "DOWN" for r in sampled)  # the short outage stays visible
    assert sampled[0].id == newest.id  # newest first, and the latest check is kept
    assert [r.checked_at for r in sampled] == sorted((r.checked_at for r in sampled), reverse=True)


def test_sampling_returns_everything_when_small(session_factory) -> None:
    store(session_factory, [result("UP", age_hours=i / 60) for i in range(5)])
    with session_factory() as db:
        assert len(sampled_results(db, "all", 1, points=10)) == 5


def test_empty_history_endpoint(client: TestClient) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    history = client.get(f"/api/monitors/{monitor_id}/history").json()
    assert history["results"] == []
    assert history["stats"]["uptime_percentage"] is None
    assert client.get(f"/api/monitors/{monitor_id}/history?points=50").json()["results"] == []
    stats = client.get("/api/stats").json()
    assert stats["pending"] == 1
    assert stats["checks"]["uptime_percentage"] is None


def test_stats_endpoint_counts(client: TestClient, mock_api) -> None:
    up_id = client.post("/api/monitors", json=monitor_payload(name="Up")).json()["id"]
    down_id = client.post("/api/monitors", json=monitor_payload(name="Down")).json()["id"]
    client.post("/api/monitors", json=monitor_payload(name="Paused", enabled=False))

    client.post(f"/api/monitors/{up_id}/check")
    mock_api.respond(500)
    client.post(f"/api/monitors/{down_id}/check")

    stats = client.get("/api/stats?window=7d").json()
    assert stats["total_monitors"] == 3
    assert stats["healthy"] == 1
    assert stats["down"] == 1
    assert stats["paused"] == 1
    assert stats["checks"]["total_checks"] == 2
    assert stats["checks"]["uptime_percentage"] == 50.0

    failures = client.get("/api/failures").json()
    assert [f["monitor_id"] for f in failures] == [down_id]
    assert failures[0]["error_category"] == "unexpected_status"


def test_history_window_filter_and_limit(client: TestClient) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    factory = client.app.state.session_factory
    with factory() as db:
        db.add_all(
            result("UP", age_hours=age, monitor_id=monitor_id) for age in (1, 30, 24 * 3, 24 * 30)
        )
        db.commit()

    def history(query: str) -> dict:
        return client.get(f"/api/monitors/{monitor_id}/history?{query}").json()

    assert history("window=24h")["stats"]["total_checks"] == 1
    assert history("window=7d")["stats"]["total_checks"] == 3
    assert history("window=all")["stats"]["total_checks"] == 4
    limited = history("window=all&limit=2")
    assert len(limited["results"]) == 2
    assert limited["stats"]["total_checks"] == 4  # stats always cover the whole window
    times = [r["checked_at"] for r in limited["results"]]
    assert times == sorted(times, reverse=True)
    assert client.get(f"/api/monitors/{monitor_id}/history?window=1y").status_code == 422
