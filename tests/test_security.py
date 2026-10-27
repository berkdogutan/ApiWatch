"""Protections for the local web server itself (the outbound SSRF rules live in
test_url_validation.py and test_checker.py)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from tests.conftest import monitor_payload


def test_default_trusted_hosts_are_local_only() -> None:
    assert Settings(_env_file=None).trusted_hosts == ["127.0.0.1", "localhost"]
    assert Settings(_env_file=None).host == "127.0.0.1"


def test_unknown_host_header_is_rejected(client: TestClient) -> None:
    # A DNS-rebinding page would reach 127.0.0.1 with its own domain in the Host header.
    response = client.get("/api/monitors", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_cross_origin_writes_are_rejected(client: TestClient) -> None:
    created = client.post(
        "/api/monitors", json=monitor_payload(), headers={"Origin": "https://attacker.example"}
    )
    assert created.status_code == 403
    assert client.get("/api/monitors").json() == []

    monitor_id = client.post("/api/monitors", json=monitor_payload()).json()["id"]
    for method, path in [
        ("POST", f"/api/monitors/{monitor_id}/check"),
        ("POST", f"/api/monitors/{monitor_id}/json-baseline/approve"),
        ("DELETE", f"/api/monitors/{monitor_id}"),
    ]:
        response = client.request(method, path, headers={"Origin": "null"})
        assert response.status_code == 403, path
    assert client.get(f"/api/monitors/{monitor_id}").status_code == 200


def test_same_origin_and_non_browser_writes_are_allowed(client: TestClient) -> None:
    same_origin = client.post(
        "/api/monitors", json=monitor_payload(), headers={"Origin": "http://testserver"}
    )
    assert same_origin.status_code == 201
    no_origin = client.post("/api/monitors", json=monitor_payload(name="From a script"))
    assert no_origin.status_code == 201


def test_pages_cannot_be_framed(client: TestClient) -> None:
    response = client.get("/")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_monitor_responses_never_expose_response_bodies(client: TestClient, mock_api) -> None:
    mock_api.respond(json={"token": "s3cr3t-value", "user": "ada"})
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    client.post(f"/api/monitors/{monitor_id}/check")
    for path in (
        f"/api/monitors/{monitor_id}",
        f"/api/monitors/{monitor_id}/history",
        f"/api/monitors/{monitor_id}/json-baseline",
    ):
        assert "s3cr3t-value" not in client.get(path).text
