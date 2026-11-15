"""Import the synthetic sleep & alertness dataset into SQLite (Stage 1A).

Reads the files written by ``data/generate_sleep_alertness.py`` and stores
them through the validated insert functions in ``backend.sleep_alertness``.

* The whole import runs in ONE transaction: any invalid row rolls back every
  row from this run, so a failed import never leaves partial data.
* Re-importing identical data is a no-op (rows are reported as skipped).
  A row whose ID matches existing data with different content aborts the
  import with ValueError; existing rows are never overwritten or deleted.
* Only synthetic rows are accepted (this importer is for the demo dataset).
* Astronauts and the mission are registered with INSERT OR IGNORE, so
  existing astronaut/mission rows (including display names) are untouched.

This importer is NOT called by ``backend.bootstrap.prepare_database``; the
dashboard's startup behavior is unchanged. Run it explicitly::

    python -m backend.import_sleep_alertness
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from backend.database import get_connection, initialize_database
from backend import sleep_alertness as sa

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "synthetic" / "sleep_alertness"

TABLES = ("sleep_sessions", "sleep_stages", "alertness_assessments",
          "reaction_time_trials", "cortisol_measurements")

REQUIRED_COLUMNS = {
    "sleep_sessions": {
        "sleep_session_id", "astronaut_id", "mission_id", "sleep_type", "bedtime_utc",
        "wake_time_utc", "estimated_sleep_minutes", "data_origin", "is_synthetic",
        "self_reported_quality", "sensor_quality", "sensor_coverage_fraction",
        "missing_data_notes", "notes",
    },
    "sleep_stages": {
        "sleep_session_id", "stage_type", "start_utc", "end_utc", "source",
        "is_estimated", "is_synthetic",
    },
    "alertness_assessments": {
        "assessment_id", "astronaut_id", "mission_id", "assessment_utc",
        "protocol_version", "timing_method", "completion_status", "data_origin",
        "is_synthetic", "lapse_threshold_ms", "false_start_threshold_ms",
        "self_reported_fatigue", "notes",
    },
    "reaction_time_trials": {
        "assessment_id", "trial_index", "interstimulus_interval_ms",
        "reaction_time_ms", "responded_before_stimulus",
    },
    "cortisol_measurements": {
        "cortisol_id", "astronaut_id", "mission_id", "sample_utc", "value", "unit",
        "sampling_method", "minutes_since_awakening", "sleep_session_id",
        "data_origin", "is_synthetic", "notes",
    },
}


# ---------------------------------------------------------------------------
# CSV parsing (blank cell -> None)
# ---------------------------------------------------------------------------

def _text(value):
    return None if value is None or value.strip() == "" else value.strip()


def _float(value, field):
    text = _text(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{field} must be numeric: {value!r}") from None


def _int(value, field):
    number = _float(value, field)
    if number is None:
        return None
    if not number.is_integer():
        raise ValueError(f"{field} must be an integer: {value!r}")
    return int(number)


def _bool(value, field):
    text = (_text(value) or "").lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    raise ValueError(f"{field} must be true or false: {value!r}")


def _read_csv(path: Path, table: str) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = REQUIRED_COLUMNS[table] - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        return list(reader)


def _require_synthetic(row, table, line):
    if not _bool(row["is_synthetic"], f"{table} line {line} is_synthetic"):
        raise ValueError(f"{table} line {line}: expected synthetic demo data")


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_sleep_alertness(data_dir: Path | str = DATA_DIR, *, db_path=None) -> dict:
    """Import the synthetic dataset; return ``{table: {"inserted", "skipped"}}``."""
    data_dir = Path(data_dir)
    manifest_path = data_dir / "scenarios.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Dataset manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("is_synthetic") is not True:
        raise ValueError("Manifest must declare is_synthetic: true")

    rows = {table: _read_csv(data_dir / f"{table}.csv", table) for table in TABLES}

    if db_path is None:
        initialize_database()
    else:
        initialize_database(db_path)

    counts = {table: {"inserted": 0, "skipped": 0} for table in TABLES}

    def tally(table, inserted, amount=1):
        counts[table]["inserted" if inserted else "skipped"] += amount

    connection = get_connection() if db_path is None else get_connection(db_path)
    with closing(connection) as conn, conn:   # single transaction
        mission = manifest["mission"]
        conn.execute(
            "INSERT OR IGNORE INTO missions (mission_id, mission_name) VALUES (?, ?)",
            (mission["mission_id"], mission.get("mission_name")),
        )
        for astronaut in manifest["astronauts"]:
            conn.execute(
                "INSERT OR IGNORE INTO astronauts (astronaut_id, display_name) VALUES (?, ?)",
                (astronaut["astronaut_id"], astronaut.get("display_name")),
            )

        for line, row in enumerate(rows["sleep_sessions"], start=2):
            _require_synthetic(row, "sleep_sessions", line)
            try:
                _, inserted = sa._add_sleep_session(
                    conn,
                    sleep_session_id=row["sleep_session_id"],
                    astronaut_id=row["astronaut_id"], mission_id=row["mission_id"],
                    sleep_type=row["sleep_type"], bedtime_utc=row["bedtime_utc"],
                    wake_time_utc=row["wake_time_utc"],
                    estimated_sleep_minutes=_float(row["estimated_sleep_minutes"],
                                                   "estimated_sleep_minutes"),
                    data_origin=row["data_origin"], is_synthetic=True,
                    self_reported_quality=_int(row["self_reported_quality"],
                                               "self_reported_quality"),
                    sensor_quality=_text(row["sensor_quality"]),
                    sensor_coverage_fraction=_float(row["sensor_coverage_fraction"],
                                                    "sensor_coverage_fraction"),
                    missing_data_notes=_text(row["missing_data_notes"]),
                    notes=_text(row["notes"]),
                )
            except ValueError as exc:
                raise ValueError(f"sleep_sessions.csv line {line}: {exc}") from None
            tally("sleep_sessions", inserted)

        stages_by_session = defaultdict(list)
        for line, row in enumerate(rows["sleep_stages"], start=2):
            _require_synthetic(row, "sleep_stages", line)
            stages_by_session[row["sleep_session_id"]].append({
                "stage_type": row["stage_type"], "start_utc": row["start_utc"],
                "end_utc": row["end_utc"], "source": row["source"],
                "is_estimated": _bool(row["is_estimated"], "is_estimated"),
                "is_synthetic": True,
            })
        for session_id, stages in stages_by_session.items():
            try:
                inserted = sa._add_sleep_stages(conn, session_id, stages)
            except ValueError as exc:
                raise ValueError(f"sleep_stages.csv session {session_id}: {exc}") from None
            tally("sleep_stages", True, inserted)
            tally("sleep_stages", False, len(stages) - inserted)

        trials_by_assessment = defaultdict(list)
        for row in rows["reaction_time_trials"]:
            trials_by_assessment[row["assessment_id"]].append({
                "trial_index": _int(row["trial_index"], "trial_index"),
                "interstimulus_interval_ms": _float(row["interstimulus_interval_ms"],
                                                    "interstimulus_interval_ms"),
                "reaction_time_ms": _float(row["reaction_time_ms"], "reaction_time_ms"),
                "responded_before_stimulus": _bool(row["responded_before_stimulus"],
                                                   "responded_before_stimulus"),
            })
        known_assessments = {row["assessment_id"] for row in rows["alertness_assessments"]}
        orphans = set(trials_by_assessment) - known_assessments
        if orphans:
            raise ValueError(f"Trials reference unknown assessments: {sorted(orphans)}")

        for line, row in enumerate(rows["alertness_assessments"], start=2):
            _require_synthetic(row, "alertness_assessments", line)
            trials = trials_by_assessment.get(row["assessment_id"], [])
            try:
                _, inserted = sa._add_alertness_assessment(
                    conn,
                    assessment_id=row["assessment_id"],
                    astronaut_id=row["astronaut_id"], mission_id=row["mission_id"],
                    assessment_utc=row["assessment_utc"], trials=trials,
                    protocol_version=row["protocol_version"],
                    timing_method=row["timing_method"],
                    completion_status=row["completion_status"],
                    data_origin=row["data_origin"], is_synthetic=True,
                    lapse_threshold_ms=_float(row["lapse_threshold_ms"], "lapse_threshold_ms"),
                    false_start_threshold_ms=_float(row["false_start_threshold_ms"],
                                                    "false_start_threshold_ms"),
                    self_reported_fatigue=_int(row["self_reported_fatigue"],
                                               "self_reported_fatigue"),
                    notes=_text(row["notes"]),
                )
            except ValueError as exc:
                raise ValueError(f"alertness_assessments.csv line {line}: {exc}") from None
            tally("alertness_assessments", inserted)
            tally("reaction_time_trials", inserted, len(trials))

        for line, row in enumerate(rows["cortisol_measurements"], start=2):
            _require_synthetic(row, "cortisol_measurements", line)
            try:
                _, inserted = sa._add_cortisol_measurement(
                    conn,
                    cortisol_id=row["cortisol_id"],
                    astronaut_id=row["astronaut_id"], mission_id=row["mission_id"],
                    sample_utc=row["sample_utc"], value=_float(row["value"], "value"),
                    unit=row["unit"], sampling_method=row["sampling_method"],
                    minutes_since_awakening=_float(row["minutes_since_awakening"],
                                                   "minutes_since_awakening"),
                    sleep_session_id=_text(row["sleep_session_id"]),
                    data_origin=row["data_origin"], is_synthetic=True,
                    notes=_text(row["notes"]),
                )
            except ValueError as exc:
                raise ValueError(f"cortisol_measurements.csv line {line}: {exc}") from None
            tally("cortisol_measurements", inserted)

    return counts


if __name__ == "__main__":
    result = import_sleep_alertness()
    print("SLEEP & ALERTNESS IMPORT COMPLETED (synthetic demo data)")
    for table, tally_ in result.items():
        print(f"{table}: {tally_['inserted']} new, {tally_['skipped']} already imported")
