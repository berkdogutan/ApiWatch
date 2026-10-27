from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.json_changes import compare_fields
from tests.conftest import MockAPI, monitor_payload

V1 = {"id": 1, "name": "Ada", "email": "ada@example.com", "active": True}
V2 = {"id": "1", "name": "Ada", "active": True, "created_at": "2026-01-01"}


def test_compare_fields_detects_all_change_kinds() -> None:
    baseline = {"id": "integer", "name": "string", "email": "string"}
    observed = {"id": "string", "name": "string", "created_at": "string"}
    assert compare_fields(baseline, observed) == [
        {"field": "created_at", "change": "added", "old_type": None, "new_type": "string"},
        {"field": "email", "change": "removed", "old_type": "string", "new_type": None},
        {"field": "id", "change": "type_changed", "old_type": "integer", "new_type": "string"},
    ]
    assert compare_fields(baseline, dict(baseline)) == []


def test_baseline_capture_and_change_detection(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    assert client.get(f"/api/monitors/{monitor_id}/json-baseline").status_code == 404

    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")
    baseline = client.get(f"/api/monitors/{monitor_id}/json-baseline").json()
    assert baseline["fields"] == {
        "id": "integer",
        "name": "string",
        "email": "string",
        "active": "boolean",
    }
    assert baseline["pending_changes"] is None
    # Only names and types are stored - never values.
    assert "ada@example.com" not in str(baseline)

    mock_api.respond(json=V2)
    client.post(f"/api/monitors/{monitor_id}/check")
    baseline = client.get(f"/api/monitors/{monitor_id}/json-baseline").json()
    kinds = {c["field"]: c["change"] for c in baseline["pending_changes"]}
    assert kinds == {"created_at": "added", "email": "removed", "id": "type_changed"}
    assert baseline["fields"]["email"] == "string"  # baseline unchanged until approval
    assert client.get(f"/api/monitors/{monitor_id}").json()["summary"]["pending_json_changes"]


def test_baseline_changes_only_after_approval(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    approve_url = f"/api/monitors/{monitor_id}/json-baseline/approve"
    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")
    assert client.post(approve_url).status_code == 409  # nothing pending

    mock_api.respond(json=V2)
    client.post(f"/api/monitors/{monitor_id}/check")
    client.post(f"/api/monitors/{monitor_id}/check")  # repeated drift doesn't auto-approve
    approved = client.post(approve_url)
    assert approved.status_code == 200
    body = approved.json()
    assert body["fields"]["id"] == "string"
    assert "email" not in body["fields"]
    assert body["pending_changes"] is None

    client.post(f"/api/monitors/{monitor_id}/check")
    assert client.get(f"/api/monitors/{monitor_id}/json-baseline").json()["pending_changes"] is None


def test_pending_drift_clears_when_response_reverts(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")
    mock_api.respond(json=V2)
    client.post(f"/api/monitors/{monitor_id}/check")
    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")
    baseline = client.get(f"/api/monitors/{monitor_id}/json-baseline").json()
    assert baseline["pending_changes"] is None


def test_non_object_json_is_ignored(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    mock_api.respond(json=[1, 2, 3])
    result = client.post(f"/api/monitors/{monitor_id}/check").json()
    assert result["status"] == "UP"
    assert result["error_category"] == "json_not_object"
    assert client.get(f"/api/monitors/{monitor_id}/json-baseline").status_code == 404


def test_disabling_inspection_discards_baseline(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")
    client.put(f"/api/monitors/{monitor_id}", json=monitor_payload(inspect_json=False))
    assert client.get(f"/api/monitors/{monitor_id}/json-baseline").status_code == 404
