from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import Monitor
from app.services.json_changes import compare_fields
from tests.conftest import MockAPI, monitor_payload

V1 = {"id": 1, "name": "Ada", "email": "ada@example.com", "active": True}
V2 = {"id": "1", "name": "Ada", "active": True, "created_at": "2026-01-01"}
V3 = {"id": "1", "name": "Ada"}


def test_compare_fields_detects_all_change_kinds() -> None:
    baseline = {"id": "number", "name": "string", "email": "string"}
    observed = {"id": "string", "name": "string", "created_at": "string"}
    assert compare_fields(baseline, observed) == [
        {"field": "created_at", "change": "added", "old_type": None, "new_type": "string"},
        {"field": "email", "change": "removed", "old_type": "string", "new_type": None},
        {"field": "id", "change": "type_changed", "old_type": "number", "new_type": "string"},
    ]
    assert compare_fields(baseline, dict(baseline)) == []


def baseline_of(client: TestClient, monitor_id: int) -> dict:
    return client.get(f"/api/monitors/{monitor_id}/json-baseline").json()


def test_baseline_capture_and_change_detection(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    assert client.get(f"/api/monitors/{monitor_id}/json-baseline").status_code == 404

    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")
    baseline = baseline_of(client, monitor_id)
    assert baseline["fields"] == {
        "id": "number",
        "name": "string",
        "email": "string",
        "active": "boolean",
    }
    assert baseline["pending_changes"] is None
    # Only names and types are stored - never values.
    assert "ada@example.com" not in str(baseline)

    mock_api.respond(json=V2)
    client.post(f"/api/monitors/{monitor_id}/check")
    baseline = baseline_of(client, monitor_id)
    kinds = {c["field"]: c["change"] for c in baseline["pending_changes"]}
    assert kinds == {"created_at": "added", "email": "removed", "id": "type_changed"}
    assert baseline["fields"]["email"] == "string"  # baseline unchanged until approval
    assert client.get(f"/api/monitors/{monitor_id}").json()["summary"]["pending_json_changes"]


def test_number_values_are_not_type_changes(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    for price in (19.99, 20, 20.5):  # many serialisers write 20.0 as 20
        mock_api.respond(json={"price": price})
        client.post(f"/api/monitors/{monitor_id}/check")
    assert baseline_of(client, monitor_id)["pending_changes"] is None


def test_baseline_changes_only_after_approval(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    approve_url = f"/api/monitors/{monitor_id}/json-baseline/approve"
    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")
    assert client.post(approve_url, json={"pending_fields": {}}).status_code == 409  # none

    mock_api.respond(json=V2)
    client.post(f"/api/monitors/{monitor_id}/check")
    client.post(f"/api/monitors/{monitor_id}/check")  # repeated drift doesn't auto-approve
    reviewed = baseline_of(client, monitor_id)["pending_fields"]
    assert client.post(approve_url).status_code == 422  # must say what was reviewed
    approved = client.post(approve_url, json={"pending_fields": reviewed})
    assert approved.status_code == 200
    body = approved.json()
    assert body["fields"]["id"] == "string"
    assert "email" not in body["fields"]
    assert body["pending_changes"] is None

    client.post(f"/api/monitors/{monitor_id}/check")
    assert baseline_of(client, monitor_id)["pending_changes"] is None


def test_approval_is_rejected_when_pending_changes_were_replaced(
    client: TestClient, mock_api: MockAPI
) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    approve_url = f"/api/monitors/{monitor_id}/json-baseline/approve"
    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")
    mock_api.respond(json=V2)
    client.post(f"/api/monitors/{monitor_id}/check")
    reviewed = baseline_of(client, monitor_id)["pending_fields"]

    mock_api.respond(json=V3)  # a newer response arrives before the user clicks approve
    client.post(f"/api/monitors/{monitor_id}/check")
    response = client.post(approve_url, json={"pending_fields": reviewed})
    assert response.status_code == 409
    assert baseline_of(client, monitor_id)["fields"]["email"] == "string"  # unchanged


def test_pending_drift_clears_when_response_reverts(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")
    mock_api.respond(json=V2)
    client.post(f"/api/monitors/{monitor_id}/check")
    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")
    assert baseline_of(client, monitor_id)["pending_changes"] is None


def test_non_object_json_is_ignored(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    mock_api.respond(json=[1, 2, 3])
    result = client.post(f"/api/monitors/{monitor_id}/check").json()
    assert result["status"] == "UP"
    assert result["error_category"] == "json_not_object"
    assert client.get(f"/api/monitors/{monitor_id}/json-baseline").status_code == 404


def test_pausing_inspection_keeps_the_baseline(client: TestClient, mock_api: MockAPI) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    mock_api.respond(json=V1)
    client.post(f"/api/monitors/{monitor_id}/check")

    client.put(f"/api/monitors/{monitor_id}", json=monitor_payload(inspect_json=False))
    mock_api.respond(json=V2)  # the API changes while inspection is off
    client.post(f"/api/monitors/{monitor_id}/check")
    assert baseline_of(client, monitor_id)["fields"]["email"] == "string"

    client.put(f"/api/monitors/{monitor_id}", json=monitor_payload(inspect_json=True))
    client.post(f"/api/monitors/{monitor_id}/check")
    baseline = baseline_of(client, monitor_id)
    assert baseline["fields"]["email"] == "string"  # not silently accepted
    assert {c["field"] for c in baseline["pending_changes"]} == {"created_at", "email", "id"}


def test_inspection_requires_get(client: TestClient) -> None:
    created = client.post("/api/monitors", json=monitor_payload(method="HEAD", inspect_json=True))
    assert created.status_code == 422
    assert "GET" in str(created.json()["detail"])

    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    updated = client.put(
        f"/api/monitors/{monitor_id}", json=monitor_payload(method="HEAD", inspect_json=True)
    )
    assert updated.status_code == 422


def test_baselines_from_version_1_0_0_do_not_report_number_changes(
    client: TestClient, mock_api: MockAPI
) -> None:
    monitor_id = client.post("/api/monitors", json=monitor_payload(inspect_json=True)).json()["id"]
    mock_api.respond(json={"id": 1, "name": "Ada"})
    client.post(f"/api/monitors/{monitor_id}/check")
    factory = client.app.state.session_factory
    with factory() as db:  # rewrite the baseline the way 1.0.0 stored it
        baseline = db.get(Monitor, monitor_id).json_baseline
        baseline.fields = {"id": "integer", "name": "string"}
        db.commit()

    client.post(f"/api/monitors/{monitor_id}/check")
    baseline = baseline_of(client, monitor_id)
    assert baseline["pending_changes"] is None
    assert baseline["fields"] == {"id": "number", "name": "string"}
