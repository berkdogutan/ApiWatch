"""Basic JSON change detection on top-level keys and value types."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import JsonBaseline, Monitor, utcnow


def compare_fields(baseline: dict[str, str], observed: dict[str, str]) -> list[dict[str, Any]]:
    """Return added / removed / type_changed entries, sorted by field name."""
    changes: list[dict[str, Any]] = []
    for key in sorted(set(baseline) | set(observed)):
        if key not in baseline:
            changes.append(
                {"field": key, "change": "added", "old_type": None, "new_type": observed[key]}
            )
        elif key not in observed:
            changes.append(
                {"field": key, "change": "removed", "old_type": baseline[key], "new_type": None}
            )
        elif baseline[key] != observed[key]:
            changes.append(
                {
                    "field": key,
                    "change": "type_changed",
                    "old_type": baseline[key],
                    "new_type": observed[key],
                }
            )
    return changes


def record_observation(db: Session, monitor: Monitor, observed: dict[str, str]) -> None:
    """Capture the first baseline, or flag drift for approval. Never auto-updates a baseline."""
    baseline = monitor.json_baseline
    if baseline is None:
        db.add(JsonBaseline(monitor_id=monitor.id, fields=observed, approved_at=utcnow()))
        return

    changes = compare_fields(baseline.fields, observed)
    if not changes:
        # The response matches the approved baseline again; drop stale pending drift.
        if baseline.pending_fields is not None:
            baseline.pending_fields = None
            baseline.pending_changes = None
            baseline.pending_detected_at = None
        return
    if baseline.pending_fields != observed:
        baseline.pending_fields = observed
        baseline.pending_changes = changes
        baseline.pending_detected_at = utcnow()


def approve_pending(baseline: JsonBaseline) -> bool:
    """Promote the pending observation to the approved baseline. Returns False if none."""
    if baseline.pending_fields is None:
        return False
    baseline.fields = baseline.pending_fields
    baseline.approved_at = utcnow()
    baseline.pending_fields = None
    baseline.pending_changes = None
    baseline.pending_detected_at = None
    return True
