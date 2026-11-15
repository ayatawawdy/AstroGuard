"""Persistent rest/attention notification events (table ``rest_notification_events``).

Events are created from ``backend.alertness_analysis`` results whose status is
``attention_needed`` or ``rest_recommended``. ``normal`` and
``insufficient_data`` results are shown live but are not stored as alerts.

Deduplication: an event's ID is a hash of (astronaut, mission, status,
analysis version, the IDs of the recent sleep records and assessments that
produced it). Re-evaluating the same stored evidence (e.g. every Streamlit
rerun) therefore maps to the same event and inserts nothing; new evidence
(a new assessment or sleep record) or a changed status yields a new event.
The selected task context changes only the wording, not the event identity.

Acknowledgment records who/when, never deletes the event, and does not mean
the astronaut is rested or safe.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone

from backend.database import get_connection
from backend.sleep_alertness import UTC_FORMAT

ALERT_STATUSES = {
    "attention_needed": ("advisory", "ATTENTION NEEDED"),
    "rest_recommended": ("warning", "REST RECOMMENDED"),
}


def _connect(db_path):
    return get_connection() if db_path is None else get_connection(db_path)


def _now() -> str:
    return datetime.now(timezone.utc).strftime(UTC_FORMAT)


def evidence_key(analysis: dict) -> str:
    evidence = analysis["evidence"]
    identity = {
        "astronaut_id": analysis["astronaut_id"],
        "mission_id": analysis["mission_id"],
        "status": analysis["status"],
        "analysis_version": analysis["analysis_version"],
        "sleep": sorted(evidence["recent_sleep_session_ids"]),
        "assessments": sorted(evidence["recent_assessment_ids"]),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def _event(row) -> dict:
    event = dict(row)
    event["evidence"] = json.loads(event.pop("evidence_json"))
    event["is_synthetic_input"] = bool(event["is_synthetic_input"])
    event["acknowledged"] = bool(event["acknowledged"])
    return event


def record_notification(analysis: dict, *, db_path=None) -> tuple[dict | None, bool]:
    """Store an alert for ``analysis`` if warranted. Returns ``(event, created)``.

    Returns ``(None, False)`` when the status does not warrant an alert.
    """
    if analysis["status"] not in ALERT_STATUSES:
        return None, False
    severity, title = ALERT_STATUSES[analysis["status"]]
    key = evidence_key(analysis)
    event_id = f"ALERT-{key[:20]}"
    evidence = {
        "supporting_observations": analysis["supporting_observations"],
        "baseline_comparisons": analysis["baseline_comparisons"],
        "rule_checks": analysis["rule_checks"],
        "record_ids": analysis["evidence"],
        "data_quality_issues": analysis["data_quality_issues"],
    }
    recent_assessments = analysis["evidence"]["recent_assessment_ids"]
    row = (
        event_id, analysis["astronaut_id"], analysis["mission_id"], _now(),
        analysis["evaluated_at_utc"], analysis["status"], severity, title,
        analysis["explanation"], analysis["suggested_action"], analysis["task_context"]["key"],
        json.dumps(evidence, sort_keys=True), key,
        recent_assessments[-1] if recent_assessments else None,
        analysis["analysis_version"], int(analysis["is_synthetic_input"]),
    )
    with closing(_connect(db_path)) as conn, conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO rest_notification_events (
                   event_id, astronaut_id, mission_id, created_utc, evaluated_at_utc,
                   alertness_status, severity, title, explanation, suggested_action,
                   task_context, evidence_json, evidence_key, source_assessment_id,
                   analysis_version, is_synthetic_input)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        created = cursor.rowcount == 1
        stored = conn.execute("SELECT * FROM rest_notification_events WHERE event_id = ?",
                              (event_id,)).fetchone()
    return _event(stored), created


def get_notifications(astronaut_id: str | None = None, *, mission_id: str | None = None,
                      unacknowledged_only: bool = False, limit: int = 200,
                      db_path=None) -> list[dict]:
    """Notification history, newest first."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer from 1 to 1000")
    query = "SELECT * FROM rest_notification_events WHERE 1 = 1"
    params: list = []
    if astronaut_id is not None:
        query += " AND astronaut_id = ?"
        params.append(astronaut_id)
    if mission_id is not None:
        query += " AND mission_id = ?"
        params.append(mission_id)
    if unacknowledged_only:
        query += " AND acknowledged = 0"
    # created_utc has 1-second resolution; ties (several alerts in the same second)
    # are broken by insertion order (rowid), not by the hash-based event_id, so the
    # newest alert is always listed first.
    query += " ORDER BY created_utc DESC, rowid DESC LIMIT ?"
    params.append(limit)
    with closing(_connect(db_path)) as conn:
        return [_event(r) for r in conn.execute(query, params)]


def count_unacknowledged(astronaut_id: str | None = None, *, mission_id: str | None = None,
                         db_path=None) -> int:
    query = "SELECT COUNT(*) FROM rest_notification_events WHERE acknowledged = 0"
    params: list = []
    if astronaut_id is not None:
        query += " AND astronaut_id = ?"
        params.append(astronaut_id)
    if mission_id is not None:
        query += " AND mission_id = ?"
        params.append(mission_id)
    with closing(_connect(db_path)) as conn:
        return conn.execute(query, params).fetchone()[0]


def acknowledge_notification(event_id: str, astronaut_id: str, *,
                             acknowledged_by: str = "Mission Control operator",
                             db_path=None) -> dict:
    """Mark an event acknowledged (idempotent; the first acknowledgment time is kept).

    ``astronaut_id`` must match the event, so one astronaut's view cannot
    acknowledge another astronaut's alert. Raises ValueError otherwise.
    """
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id is required")
    if not isinstance(acknowledged_by, str) or not acknowledged_by.strip():
        raise ValueError("acknowledged_by is required")
    with closing(_connect(db_path)) as conn, conn:
        row = conn.execute("SELECT * FROM rest_notification_events WHERE event_id = ?",
                           (event_id,)).fetchone()
        if row is None or row["astronaut_id"] != astronaut_id:
            raise ValueError(f"No notification {event_id!r} for astronaut {astronaut_id!r}")
        if not row["acknowledged"]:
            conn.execute(
                "UPDATE rest_notification_events SET acknowledged = 1, acknowledged_utc = ?, "
                "acknowledged_by = ? WHERE event_id = ? AND acknowledged = 0",
                (_now(), acknowledged_by.strip(), event_id),
            )
        row = conn.execute("SELECT * FROM rest_notification_events WHERE event_id = ?",
                           (event_id,)).fetchone()
    return _event(row)
