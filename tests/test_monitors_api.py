from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import url_validation
from tests.conftest import monitor_payload


def test_create_monitor(client: TestClient) -> None:
    response = client.post("/api/monitors", json=monitor_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["name"] == "Example API"
    assert body["interval_seconds"] == 60
    assert body["created_at"].endswith("Z")
    assert body["summary"]["current_status"] == "PENDING"
    assert body["summary"]["uptime_24h"] is None

    listed = client.get("/api/monitors").json()
    assert [m["name"] for m in listed] == ["Example API"]


def test_create_monitor_defaults(client: TestClient) -> None:
    body = client.post(
        "/api/monitors", json={"name": "Minimal", "url": "https://api.example.com/"}
    ).json()
    assert body["method"] == "GET"
    assert body["expected_status"] == 200
    assert body["interval_seconds"] == 60
    assert body["enabled"] is True


def test_create_monitor_validation_errors(client: TestClient) -> None:
    too_fast = client.post("/api/monitors", json=monitor_payload(interval_seconds=10))
    assert too_fast.status_code == 422
    assert too_fast.json()["detail"][0]["loc"][-1] == "interval_seconds"

    too_slow = client.post("/api/monitors", json=monitor_payload(interval_seconds=7200))
    assert too_slow.status_code == 422

    bad_method = client.post("/api/monitors", json=monitor_payload(method="POST"))
    assert bad_method.status_code == 422

    bad_scheme = client.post("/api/monitors", json=monitor_payload(url="ftp://api.example.com"))
    assert bad_scheme.status_code == 422
    assert bad_scheme.json()["detail"][0]["loc"] == ["body", "url"]

    internal = client.post("/api/monitors", json=monitor_payload(url="http://169.254.169.254/"))
    assert internal.status_code == 422

    blank = client.post("/api/monitors", json=monitor_payload(name="   "))
    assert blank.status_code == 422


def test_duplicate_name_conflict(client: TestClient) -> None:
    assert client.post("/api/monitors", json=monitor_payload()).status_code == 201
    assert client.post("/api/monitors", json=monitor_payload()).status_code == 409


def test_update_monitor(client: TestClient) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    response = client.put(
        f"/api/monitors/{monitor_id}",
        json=monitor_payload(name="Renamed", interval_seconds=300, method="HEAD"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["interval_seconds"] == 300
    assert body["method"] == "HEAD"
    assert client.get(f"/api/monitors/{monitor_id}").json()["name"] == "Renamed"


def test_update_rejects_unsafe_url(client: TestClient) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    response = client.put(
        f"/api/monitors/{monitor_id}", json=monitor_payload(url="http://localhost:8000/")
    )
    assert response.status_code == 422
    assert client.get(f"/api/monitors/{monitor_id}").json()["url"].startswith("https://api.")


def test_put_requires_every_field(client: TestClient) -> None:
    monitor_id = client.post(
        "/api/monitors", json=monitor_payload(interval_seconds=300, inspect_json=True)
    ).json()["id"]
    partial = {"name": "Example API", "url": "https://api.example.com/health", "enabled": False}
    response = client.put(f"/api/monitors/{monitor_id}", json=partial)
    assert response.status_code == 422
    missing = {e["loc"][-1] for e in response.json()["detail"] if e["type"] == "missing"}
    assert {"interval_seconds", "inspect_json", "method"} <= missing
    # Nothing was silently reset to defaults.
    stored = client.get(f"/api/monitors/{monitor_id}").json()
    assert (stored["interval_seconds"], stored["inspect_json"], stored["enabled"]) == (
        300,
        True,
        True,
    )


def test_delete_monitor_removes_history(client: TestClient) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    assert client.post(f"/api/monitors/{monitor_id}/check").status_code == 200
    assert client.delete(f"/api/monitors/{monitor_id}").status_code == 204
    assert client.get(f"/api/monitors/{monitor_id}").status_code == 404
    assert client.get(f"/api/monitors/{monitor_id}/history").status_code == 404
    assert client.get("/api/failures").json() == []
    assert client.delete(f"/api/monitors/{monitor_id}").status_code == 404


def test_missing_monitor_returns_404(client: TestClient) -> None:
    assert client.get("/api/monitors/999").status_code == 404
    assert client.post("/api/monitors/999/check").status_code == 404
    assert client.get("/monitors/999").status_code == 404


def test_manual_check_records_history(client: TestClient) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    result = client.post(f"/api/monitors/{monitor_id}/check").json()
    assert result["status"] == "UP"
    assert result["status_code"] == 200
    assert result["response_time_ms"] >= 0

    history = client.get(f"/api/monitors/{monitor_id}/history?window=all").json()
    assert history["stats"]["total_checks"] == 1
    assert history["results"][0]["id"] == result["id"]
    summary = client.get(f"/api/monitors/{monitor_id}").json()["summary"]
    assert summary["current_status"] == "UP"
    assert summary["uptime_24h"] == 100.0


def test_disabled_monitor_can_still_be_checked_manually(client: TestClient) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(enabled=False)).json()["id"]
    assert client.post(f"/api/monitors/{monitor_id}/check").json()["status"] == "UP"


def test_dashboard_pages_render(client: TestClient) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    home = client.get("/")
    assert home.status_code == 200
    assert "API Health Overview" in home.text
    detail = client.get(f"/monitors/{monitor_id}")
    assert detail.status_code == 200
    assert "Example API" in detail.text
    assert client.get("/static/js/chart.js").status_code == 200


def test_health_endpoint(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_monitor_with_failing_dns_can_still_be_paused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]

    def nxdomain(host: str, port: int) -> list[str]:
        raise OSError("getaddrinfo failed")

    monkeypatch.setattr(url_validation, "resolve_host", nxdomain)  # the host stopped resolving
    paused = client.put(f"/api/monitors/{monitor_id}", json=monitor_payload(enabled=False))
    assert paused.status_code == 200
    assert paused.json()["enabled"] is False
    # A *new* URL is still validated.
    moved = client.put(
        f"/api/monitors/{monitor_id}", json=monitor_payload(url="https://moved.example.com/")
    )
    assert moved.status_code == 422


def test_out_of_range_ids_are_rejected(client: TestClient) -> None:
    too_big = 2**63
    assert client.get(f"/api/monitors/{too_big}").status_code == 422
    assert client.get(f"/api/failures?monitor_id={too_big}").status_code == 422
    assert client.get("/api/monitors/0").status_code == 422
