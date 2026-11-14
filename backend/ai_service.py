"""Read-only SQLite-to-AI integration; no clinical aggregation assumptions."""

from contextlib import closing
from datetime import date, datetime, timezone

import pandas as pd

from ai import analyze_health_data
from ai.analysis import METRICS, REQUIRED
from backend.database import DB_PATH, get_connection


UNITS = {"sleep_duration": "hours", "heart_rate": "bpm",
         "stress_level": "1-10", "activity_minutes": "minutes"}


def analyze_astronaut_day(astronaut_id, mission_id, target_day, *,
                          aggregation="reject", db_path=DB_PATH):
    """Analyze one UTC day with all earlier days from the same mission.

    target_day is an ISO YYYY-MM-DD string. Duplicate daily metrics raise unless
    aggregation='latest' is explicitly selected. See backend/README.md.
    """
    for label, value in (("astronaut_id", astronaut_id), ("mission_id", mission_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a nonempty string")
    if aggregation not in ("reject", "latest"):
        raise ValueError("aggregation must be 'reject' or 'latest'")
    try:
        target = date.fromisoformat(target_day)
        if target.isoformat() != target_day:
            raise ValueError("use YYYY-MM-DD")
    except (ValueError, TypeError) as exc:
        raise ValueError("target_day must be YYYY-MM-DD") from exc
    now = datetime.now(timezone.utc)
    if target > now.date():
        raise ValueError("target_day cannot be in the future")

    # Do not use the CRUD retrieval limit: it can truncate historical days.
    # Parse timestamps rather than comparing differently formatted ISO strings.
    with closing(get_connection(db_path)) as conn:
        if conn.execute("SELECT 1 FROM astronauts WHERE astronaut_id = ?",
                        (astronaut_id,)).fetchone() is None:
            raise ValueError("Unknown astronaut")
        if conn.execute("SELECT 1 FROM missions WHERE mission_id = ?",
                        (mission_id,)).fetchone() is None:
            raise ValueError("Unknown mission")
        rows = conn.execute(
            """SELECT measurement_id, timestamp_utc, metric_type, value, unit
               FROM measurements WHERE astronaut_id = ? AND mission_id = ?
               AND metric_type IN (?, ?, ?, ?)""",
            (astronaut_id, mission_id, *METRICS),
        ).fetchall()

    selected = {}
    for row in rows:
        try:
            timestamp = datetime.fromisoformat(row["timestamp_utc"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("timezone required")
            timestamp = timestamp.astimezone(timezone.utc)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"Invalid timestamp for {row['measurement_id']}") from exc
        day = timestamp.date()
        if day > target or timestamp > now:
            continue
        if row["unit"] != UNITS[row["metric_type"]]:
            raise ValueError(f"Invalid unit for {row['measurement_id']}")
        key = (day, row["metric_type"])
        candidate = (timestamp, row["measurement_id"], row["value"])
        if key in selected:
            if aggregation == "reject":
                raise ValueError(f"Duplicate daily metric: {day} {row['metric_type']}; select an aggregation policy")
            if candidate[:2] <= selected[key][:2]:
                continue
        selected[key] = candidate

    daily = {}
    for (day, metric), (_, _, value) in selected.items():
        daily.setdefault(day, {"astronaut_id": astronaut_id, "date": day.isoformat(),
                               **{m: None for m in METRICS}})[metric] = value
    # Even a wholly missing target day must produce an insufficient-data result.
    current = daily.pop(target, {"astronaut_id": astronaut_id, "date": target.isoformat(),
                                 **{m: None for m in METRICS}})
    history = pd.DataFrame([daily[day] for day in sorted(daily)],
                           columns=[*REQUIRED, "activity_minutes"])
    return analyze_health_data(history, [current])
