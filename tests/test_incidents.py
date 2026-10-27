from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

from tests.conftest import MockAPI, monitor_payload


def open_incidents(client: TestClient, monitor_id: int) -> list[dict]:
    return client.get(f"/api/incidents?status=open&monitor_id={monitor_id}").json()


def test_incident_opens_after_two_consecutive_down_checks(
    client: TestClient, mock_api: MockAPI
) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    mock_api.respond(500)

    first = client.post(f"/api/monitors/{monitor_id}/check").json()
    assert first["status"] == "DOWN"
    assert open_incidents(client, monitor_id) == []  # one failure is not an incident

    client.post(f"/api/monitors/{monitor_id}/check")
    incidents = open_incidents(client, monitor_id)
    assert len(incidents) == 1
    assert incidents[0]["reason"] == "Expected HTTP 200, got 500"
    assert incidents[0]["started_at"] == first["checked_at"]  # starts at the first failure
    assert client.get(f"/api/monitors/{monitor_id}").json()["summary"]["has_open_incident"]


def test_no_duplicate_open_incidents(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    mock_api.respond(500)
    for _ in range(5):
        client.post(f"/api/monitors/{monitor_id}/check")
    assert len(client.get("/api/incidents").json()) == 1


def test_interrupted_failures_do_not_open_incident(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    for status in (500, 200, 500, 200):
        mock_api.respond(status)
        client.post(f"/api/monitors/{monitor_id}/check")
    assert client.get("/api/incidents").json() == []


def test_incident_resolves_on_recovery(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    mock_api.respond(500)
    client.post(f"/api/monitors/{monitor_id}/check")
    client.post(f"/api/monitors/{monitor_id}/check")

    mock_api.respond(200)
    recovery = client.post(f"/api/monitors/{monitor_id}/check").json()
    assert recovery["status"] == "UP"

    assert open_incidents(client, monitor_id) == []
    resolved = client.get("/api/incidents?status=resolved").json()
    assert len(resolved) == 1
    assert resolved[0]["resolved_at"] == recovery["checked_at"]
    assert resolved[0]["duration_seconds"] >= 0
    assert resolved[0]["is_open"] is False

    # A new outage creates a new incident rather than reopening the old one.
    mock_api.respond(503)
    client.post(f"/api/monitors/{monitor_id}/check")
    client.post(f"/api/monitors/{monitor_id}/check")
    assert len(client.get("/api/incidents").json()) == 2


def test_degraded_check_resolves_incident(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(slow_threshold_ms=1)).json()[
        "id"
    ]
    mock_api.respond(500)
    client.post(f"/api/monitors/{monitor_id}/check")
    client.post(f"/api/monitors/{monitor_id}/check")
    assert len(open_incidents(client, monitor_id)) == 1

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)
        return httpx.Response(200)

    mock_api.handler = slow
    assert client.post(f"/api/monitors/{monitor_id}/check").json()["status"] == "DEGRADED"
    assert open_incidents(client, monitor_id) == []
