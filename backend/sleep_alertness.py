"""Stage 1A sleep, alertness and cortisol record storage.

Insert and retrieval functions for the tables defined in
``backend.database.SLEEP_ALERTNESS_SCHEMA``. The full contract is documented
in ``docs/contracts.md``.

All checks in this module are *technical data-integrity* rules (valid
timestamps, consistent provenance, non-overlapping intervals). None of them is
a clinical threshold, and no function here interprets sleep, reaction time or
cortisol values medically.

Conventions
-----------
* Timestamps are accepted as timezone-aware ISO 8601 strings or aware
  ``datetime`` objects, converted to UTC and stored as
  ``YYYY-MM-DDTHH:MM:SSZ``. Naive timestamps, sub-second precision and future
  timestamps are rejected.
* Every public function accepts ``db_path=`` (``None`` = configured database).
  The database must already be initialized with
  ``backend.database.initialize_database``.
* Inserts are idempotent: re-inserting an identical record is a no-op that
  returns the existing ID. A record whose ID or natural key matches an
  existing record with *different* content raises ``ValueError``.
* Invalid input raises ``ValueError``; ``sqlite3.Error`` propagates.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from backend.database import get_connection

# ---------------------------------------------------------------------------
# Vocabularies (must match the CHECK constraints in backend/database.py)
# ---------------------------------------------------------------------------

SLEEP_TYPES = ("main", "nap")
SLEEP_DATA_ORIGINS = ("manual", "wearable", "synthetic")
SENSOR_QUALITIES = ("good", "fair", "poor", "unusable")
STAGE_TYPES = ("awake", "light", "deep", "rem", "unknown")
STAGE_SOURCES = ("wearable", "synthetic")

ALERTNESS_DATA_ORIGINS = ("app", "synthetic")
TIMING_METHODS = (
    "software_monotonic_clock",
    "browser_event_timestamp",
    "synthetic_generator",
    "unknown",
)
COMPLETION_STATUSES = ("completed", "incomplete")
TRIAL_OUTCOMES = ("valid", "lapse", "false_start")

CORTISOL_UNITS = ("nmol/L", "ug/dL")
CORTISOL_SAMPLING_METHODS = ("saliva", "blood_serum", "blood_plasma", "other")
CORTISOL_DATA_ORIGINS = ("manual", "synthetic")

# Technical plausibility limits (data-entry guards, NOT clinical thresholds).
MAX_SLEEP_SESSION_HOURS = 24
MAX_REACTION_TIME_MS = 60_000.0
MAX_TRIALS_PER_ASSESSMENT = 1_000
# Tolerance when a supplied minutes_since_awakening is compared with the
# value derived from the linked sleep session's wake time.
AWAKENING_TOLERANCE_MINUTES = 1.0

# Default PVT-style scoring parameters. These are protocol definitions used to
# label trials (a conventional PVT-literature choice), stored with every
# assessment. They are not validated for AstroGuard and are not alert levels.
DEFAULT_LAPSE_THRESHOLD_MS = 500.0
DEFAULT_FALSE_START_THRESHOLD_MS = 100.0

UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


# ---------------------------------------------------------------------------
# Generic validation helpers
# ---------------------------------------------------------------------------

def _connect(db_path):
    return get_connection() if db_path is None else get_connection(db_path)


def _require_id(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    if value != value.strip():
        raise ValueError(f"{field} must not have leading/trailing whitespace")
    return value


def _optional_text(value, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or None")
    return value.strip() or None


def _require_choice(value, field: str, allowed: Sequence[str]) -> str:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(allowed)}; got {value!r}")
    return value


def _require_bool(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be True or False")
    return value


def _optional_number(value, field: str, *, minimum=None, maximum=None,
                     strictly_positive=False) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric or None")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    if strictly_positive and number <= 0:
        raise ValueError(f"{field} must be greater than 0")
    if minimum is not None and number < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return number


def _optional_int_scale(value, field: str, low: int, high: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer from {low} to {high} or None")
    if not low <= value <= high:
        raise ValueError(f"{field} must be an integer from {low} to {high}")
    return value


def normalize_utc(value, field: str = "timestamp", *, allow_future: bool = False) -> str:
    """Return ``value`` as normalized UTC text ``YYYY-MM-DDTHH:MM:SSZ``.

    Accepts aware ``datetime`` objects or ISO 8601 strings with an explicit
    offset (``Z`` or ``+HH:MM``). Raises ValueError for naive, malformed,
    sub-second or (unless ``allow_future``) future timestamps.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.strip())
        except ValueError:
            raise ValueError(f"{field} is not a valid ISO 8601 timestamp: {value!r}") from None
    else:
        raise ValueError(f"{field} must be an ISO 8601 string or datetime")

    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone (e.g. 'Z' for UTC)")
    moment = moment.astimezone(timezone.utc)
    if moment.microsecond:
        raise ValueError(f"{field} must have whole-second precision")
    if moment.year < 1970:
        raise ValueError(f"{field} is implausibly early")
    if not allow_future and moment > datetime.now(timezone.utc):
        raise ValueError(f"{field} must not be in the future")
    return moment.strftime(UTC_FORMAT)


def _parse_utc(text: str) -> datetime:
    return datetime.strptime(text, UTC_FORMAT).replace(tzinfo=timezone.utc)


def _minutes_between(start: str, end: str) -> float:
    return (_parse_utc(end) - _parse_utc(start)).total_seconds() / 60.0


def _require_astronaut_and_mission(conn, astronaut_id: str, mission_id: str) -> None:
    astronaut = conn.execute(
        "SELECT 1 FROM astronauts WHERE astronaut_id = ?", (astronaut_id,)
    ).fetchone()
    mission = conn.execute(
        "SELECT 1 FROM missions WHERE mission_id = ?", (mission_id,)
    ).fetchone()
    if astronaut is None or mission is None:
        raise ValueError(f"Unknown astronaut or mission: {astronaut_id!r}, {mission_id!r}")


def _insert_or_verify(conn, table: str, row: dict, key_cols: Sequence[str],
                      natural_key_cols: Sequence[str] | None = None) -> tuple[dict, bool]:
    """Insert ``row`` unless an identical record already exists.

    Returns ``(stored_row, inserted)``. If a record with the same primary key
    or natural key exists with different content, raises ValueError. Records
    matched by natural key are compared without their primary-key columns.
    """
    where = " AND ".join(f"{col} = ?" for col in key_cols)
    existing = conn.execute(
        f"SELECT * FROM {table} WHERE {where}", [row[c] for c in key_cols]
    ).fetchone()
    if existing is not None:
        if dict(existing) != row:
            raise ValueError(
                f"Conflicting {table} record for {dict(zip(key_cols, (row[c] for c in key_cols)))}"
            )
        return dict(existing), False

    if natural_key_cols:
        where = " AND ".join(f"{col} = ?" for col in natural_key_cols)
        existing = conn.execute(
            f"SELECT * FROM {table} WHERE {where}", [row[c] for c in natural_key_cols]
        ).fetchone()
        if existing is not None:
            stored = dict(existing)
            strip = lambda r: {k: v for k, v in r.items() if k not in key_cols}
            if strip(stored) != strip(row):
                raise ValueError(
                    f"Duplicate {table} record with different content for "
                    f"{dict(zip(natural_key_cols, (row[c] for c in natural_key_cols)))}"
                )
            return stored, False

    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(row.values()))
    return row, True


# ---------------------------------------------------------------------------
# Sleep sessions
# ---------------------------------------------------------------------------

def _add_sleep_session(conn, *, astronaut_id, mission_id, bedtime_utc, wake_time_utc,
                       data_origin, is_synthetic=False, sleep_type="main",
                       estimated_sleep_minutes=None, self_reported_quality=None,
                       sensor_quality=None, sensor_coverage_fraction=None,
                       missing_data_notes=None, notes=None,
                       sleep_session_id=None) -> tuple[str, bool]:
    astronaut_id = _require_id(astronaut_id, "astronaut_id")
    mission_id = _require_id(mission_id, "mission_id")
    sleep_session_id = (
        f"SLP-{uuid4().hex}" if sleep_session_id is None
        else _require_id(sleep_session_id, "sleep_session_id")
    )
    _require_choice(sleep_type, "sleep_type", SLEEP_TYPES)
    _require_choice(data_origin, "data_origin", SLEEP_DATA_ORIGINS)
    _require_bool(is_synthetic, "is_synthetic")
    if data_origin == "synthetic" and not is_synthetic:
        raise ValueError("data_origin 'synthetic' requires is_synthetic=True")

    bedtime = normalize_utc(bedtime_utc, "bedtime_utc")
    wake = normalize_utc(wake_time_utc, "wake_time_utc")
    time_in_bed = _minutes_between(bedtime, wake)
    if time_in_bed <= 0:
        raise ValueError("wake_time_utc must be later than bedtime_utc")
    if time_in_bed > MAX_SLEEP_SESSION_HOURS * 60:
        raise ValueError(f"Sleep session longer than {MAX_SLEEP_SESSION_HOURS} hours")

    estimate = _optional_number(estimated_sleep_minutes, "estimated_sleep_minutes", minimum=0)
    if estimate is not None and estimate > time_in_bed:
        raise ValueError("estimated_sleep_minutes cannot exceed time in bed")

    quality = _optional_int_scale(self_reported_quality, "self_reported_quality", 1, 5)
    if sensor_quality is not None:
        _require_choice(sensor_quality, "sensor_quality", SENSOR_QUALITIES)
    coverage = _optional_number(
        sensor_coverage_fraction, "sensor_coverage_fraction", minimum=0, maximum=1
    )

    if data_origin == "manual" and (sensor_quality is not None or coverage is not None):
        raise ValueError("Manual sleep logs cannot carry sensor_quality or sensor_coverage_fraction")
    if data_origin == "wearable" and sensor_quality is None:
        raise ValueError("Wearable sleep sessions require sensor_quality")
    if estimate is not None and (sensor_quality == "unusable" or coverage == 0):
        raise ValueError(
            "Conflicting sensor observation: an unusable or zero-coverage signal "
            "cannot support an estimated sleep duration"
        )

    row = {
        "sleep_session_id": sleep_session_id,
        "astronaut_id": astronaut_id,
        "mission_id": mission_id,
        "sleep_type": sleep_type,
        "bedtime_utc": bedtime,
        "wake_time_utc": wake,
        "estimated_sleep_minutes": estimate,
        "data_origin": data_origin,
        "is_synthetic": int(is_synthetic),
        "self_reported_quality": quality,
        "sensor_quality": sensor_quality,
        "sensor_coverage_fraction": coverage,
        "missing_data_notes": _optional_text(missing_data_notes, "missing_data_notes"),
        "notes": _optional_text(notes, "notes"),
    }

    _require_astronaut_and_mission(conn, astronaut_id, mission_id)

    # Idempotent re-insert or conflicting ID / natural key.
    exists = conn.execute(
        """SELECT 1 FROM sleep_sessions WHERE sleep_session_id = ?
           OR (astronaut_id = ? AND data_origin = ? AND bedtime_utc = ?)""",
        (sleep_session_id, astronaut_id, data_origin, bedtime),
    ).fetchone()
    if exists is None:
        # Same astronaut, same data origin, overlapping interval = conflicting
        # observations. Different origins (e.g. a manual log and a wearable
        # recording of the same night) may legitimately coexist.
        overlap = conn.execute(
            """SELECT sleep_session_id FROM sleep_sessions
               WHERE astronaut_id = ? AND data_origin = ?
                 AND julianday(bedtime_utc) < julianday(?)
                 AND julianday(wake_time_utc) > julianday(?)
               LIMIT 1""",
            (astronaut_id, data_origin, wake, bedtime),
        ).fetchone()
        if overlap is not None:
            raise ValueError(
                f"Conflicting {data_origin} sleep session: interval overlaps "
                f"{overlap['sleep_session_id']}"
            )

    stored, inserted = _insert_or_verify(
        conn, "sleep_sessions", row, ["sleep_session_id"],
        ["astronaut_id", "data_origin", "bedtime_utc"],
    )
    return stored["sleep_session_id"], inserted


def add_sleep_session(astronaut_id: str, mission_id: str, bedtime_utc, wake_time_utc, *,
                      data_origin: str, is_synthetic: bool = False, sleep_type: str = "main",
                      estimated_sleep_minutes: float | None = None,
                      self_reported_quality: int | None = None,
                      sensor_quality: str | None = None,
                      sensor_coverage_fraction: float | None = None,
                      missing_data_notes: str | None = None, notes: str | None = None,
                      sleep_session_id: str | None = None, db_path=None) -> str:
    """Store one sleep period (main sleep or nap) and return its ID.

    ``bedtime_utc``/``wake_time_utc`` bound *time in bed*; they are not
    confirmed sleep. ``estimated_sleep_minutes`` is the optional estimate of
    actual sleep (self-reported for manual logs, device-estimated for
    wearables). Sessions may cross midnight. See docs/contracts.md.
    """
    with closing(_connect(db_path)) as conn, conn:
        session_id, _ = _add_sleep_session(
            conn, astronaut_id=astronaut_id, mission_id=mission_id,
            bedtime_utc=bedtime_utc, wake_time_utc=wake_time_utc,
            data_origin=data_origin, is_synthetic=is_synthetic, sleep_type=sleep_type,
            estimated_sleep_minutes=estimated_sleep_minutes,
            self_reported_quality=self_reported_quality,
            sensor_quality=sensor_quality,
            sensor_coverage_fraction=sensor_coverage_fraction,
            missing_data_notes=missing_data_notes, notes=notes,
            sleep_session_id=sleep_session_id,
        )
    return session_id


# ---------------------------------------------------------------------------
# Sleep stages
# ---------------------------------------------------------------------------

_STAGE_KEYS = {"stage_type", "start_utc", "end_utc", "source", "is_estimated", "is_synthetic"}


def _add_sleep_stages(conn, sleep_session_id, stages) -> int:
    sleep_session_id = _require_id(sleep_session_id, "sleep_session_id")
    if isinstance(stages, (str, bytes, Mapping)) or not isinstance(stages, Sequence):
        raise ValueError("stages must be a sequence of mappings")

    session = conn.execute(
        "SELECT * FROM sleep_sessions WHERE sleep_session_id = ?", (sleep_session_id,)
    ).fetchone()
    if session is None:
        raise ValueError(f"Unknown sleep session: {sleep_session_id!r}")
    if session["data_origin"] == "manual":
        raise ValueError("Manual sleep logs cannot have sensor-estimated sleep stages")

    source = session["data_origin"]
    is_synthetic = session["is_synthetic"]
    bedtime, wake = session["bedtime_utc"], session["wake_time_utc"]

    rows = []
    for position, stage in enumerate(stages, start=1):
        if not isinstance(stage, Mapping):
            raise ValueError(f"Stage {position} must be a mapping")
        unknown = set(stage) - _STAGE_KEYS
        if unknown:
            raise ValueError(f"Stage {position} has unknown fields: {sorted(unknown)}")
        _require_choice(stage.get("stage_type"), "stage_type", STAGE_TYPES)
        start = normalize_utc(stage.get("start_utc"), "stage start_utc")
        end = normalize_utc(stage.get("end_utc"), "stage end_utc")
        if end <= start:
            raise ValueError(f"Stage {position}: end_utc must be later than start_utc")
        if start < bedtime or end > wake:
            raise ValueError(f"Stage {position} lies outside its sleep session")
        if stage.get("source", source) != source:
            raise ValueError(f"Stage {position}: source must match session origin {source!r}")
        if stage.get("is_estimated", True) is not True:
            raise ValueError(
                "Sleep stages are stored only as estimates; "
                "clinically verified staging is not supported"
            )
        if "is_synthetic" in stage and _require_bool(stage["is_synthetic"], "is_synthetic") != bool(is_synthetic):
            raise ValueError(f"Stage {position}: is_synthetic must match its sleep session")
        rows.append({
            "sleep_session_id": sleep_session_id,
            "start_utc": start,
            "end_utc": end,
            "stage_type": stage["stage_type"],
            "source": source,
            "is_estimated": 1,
            "is_synthetic": is_synthetic,
        })

    existing = [dict(r) for r in conn.execute(
        "SELECT * FROM sleep_stages WHERE sleep_session_id = ?", (sleep_session_id,)
    )]
    new_rows = [r for r in rows if r not in existing]
    # Fixed-width normalized UTC text sorts chronologically.
    intervals = sorted(existing + new_rows, key=lambda r: r["start_utc"])
    for earlier, later in zip(intervals, intervals[1:]):
        if later["start_utc"] < earlier["end_utc"]:
            raise ValueError(
                f"Overlapping or conflicting sleep stages at {later['start_utc']}"
            )

    inserted = 0
    for row in new_rows:
        _, was_inserted = _insert_or_verify(
            conn, "sleep_stages", row, ["sleep_session_id", "start_utc"]
        )
        inserted += was_inserted
    return inserted


def add_sleep_stages(sleep_session_id: str, stages: Sequence[Mapping], *, db_path=None) -> int:
    """Attach estimated sleep-stage intervals to a wearable/synthetic session.

    Each stage is ``{"stage_type", "start_utc", "end_utc"}``; ``source``,
    ``is_estimated`` and ``is_synthetic`` are derived from the session (and
    verified if supplied). Stages must lie inside the session and must not
    overlap. All stages are stored atomically. Returns the number of new rows
    (identical stages already stored are skipped). Stages are optional: a
    session without stages is valid.
    """
    with closing(_connect(db_path)) as conn, conn:
        return _add_sleep_stages(conn, sleep_session_id, stages)


# ---------------------------------------------------------------------------
# Alertness assessments and reaction-time trials
# ---------------------------------------------------------------------------

_TRIAL_KEYS = {"trial_index", "interstimulus_interval_ms", "reaction_time_ms",
               "responded_before_stimulus"}


def classify_trial(reaction_time_ms, responded_before_stimulus, *,
                   lapse_threshold_ms, false_start_threshold_ms) -> str:
    """Label one trial ``valid``, ``lapse`` or ``false_start`` (protocol scoring)."""
    if responded_before_stimulus:
        return "false_start"
    if reaction_time_ms is None:
        return "lapse"  # no response before the trial timed out
    if reaction_time_ms < false_start_threshold_ms:
        return "false_start"
    if reaction_time_ms >= lapse_threshold_ms:
        return "lapse"
    return "valid"


def summarize_trials(trials: Sequence[Mapping]) -> dict:
    """Summary statistics over classified trial rows.

    Response-time statistics use every recorded response that is not a false
    start (valid responses and slow-response lapses). Timed-out trials have
    no response time and only count as lapses.
    """
    rts = [t["reaction_time_ms"] for t in trials
           if t["outcome"] != "false_start" and t["reaction_time_ms"] is not None]
    rnd = lambda value: None if value is None else round(float(value), 3)
    return {
        "trial_count": len(trials),
        "response_count": len(rts),
        "lapse_count": sum(t["outcome"] == "lapse" for t in trials),
        "false_start_count": sum(t["outcome"] == "false_start" for t in trials),
        "mean_rt_ms": rnd(statistics.fmean(rts)) if rts else None,
        "median_rt_ms": rnd(statistics.median(rts)) if rts else None,
        "sd_rt_ms": rnd(statistics.stdev(rts)) if len(rts) >= 2 else None,
        "min_rt_ms": rnd(min(rts)) if rts else None,
        "max_rt_ms": rnd(max(rts)) if rts else None,
    }


def _add_alertness_assessment(conn, *, astronaut_id, mission_id, assessment_utc, trials,
                              protocol_version, timing_method, data_origin="app",
                              is_synthetic=False, completion_status="completed",
                              lapse_threshold_ms=DEFAULT_LAPSE_THRESHOLD_MS,
                              false_start_threshold_ms=DEFAULT_FALSE_START_THRESHOLD_MS,
                              self_reported_fatigue=None, notes=None,
                              assessment_id=None) -> tuple[str, bool]:
    astronaut_id = _require_id(astronaut_id, "astronaut_id")
    mission_id = _require_id(mission_id, "mission_id")
    assessment_id = (
        f"ALR-{uuid4().hex}" if assessment_id is None
        else _require_id(assessment_id, "assessment_id")
    )
    timestamp = normalize_utc(assessment_utc, "assessment_utc")
    protocol_version = _require_id(protocol_version, "protocol_version")
    _require_choice(timing_method, "timing_method", TIMING_METHODS)
    _require_choice(data_origin, "data_origin", ALERTNESS_DATA_ORIGINS)
    _require_choice(completion_status, "completion_status", COMPLETION_STATUSES)
    _require_bool(is_synthetic, "is_synthetic")
    if data_origin == "synthetic" and not is_synthetic:
        raise ValueError("data_origin 'synthetic' requires is_synthetic=True")
    if timing_method == "synthetic_generator" and not is_synthetic:
        raise ValueError("timing_method 'synthetic_generator' requires is_synthetic=True")

    lapse_ms = _optional_number(lapse_threshold_ms, "lapse_threshold_ms", strictly_positive=True)
    false_ms = _optional_number(false_start_threshold_ms, "false_start_threshold_ms", minimum=0)
    if lapse_ms is None or false_ms is None:
        raise ValueError("Lapse and false-start thresholds are required")
    if false_ms >= lapse_ms:
        raise ValueError("false_start_threshold_ms must be below lapse_threshold_ms")
    fatigue = _optional_int_scale(self_reported_fatigue, "self_reported_fatigue", 1, 7)

    if isinstance(trials, (str, bytes, Mapping)) or not isinstance(trials, Sequence) or not trials:
        raise ValueError("At least one reaction-time trial is required")
    if len(trials) > MAX_TRIALS_PER_ASSESSMENT:
        raise ValueError(f"At most {MAX_TRIALS_PER_ASSESSMENT} trials per assessment")

    trial_rows = []
    seen_indexes = set()
    for position, trial in enumerate(trials, start=1):
        if not isinstance(trial, Mapping):
            raise ValueError(f"Trial {position} must be a mapping")
        unknown = set(trial) - _TRIAL_KEYS
        if unknown:
            raise ValueError(f"Trial {position} has unknown fields: {sorted(unknown)}")
        index = trial.get("trial_index", position)
        if isinstance(index, bool) or not isinstance(index, int) or index < 1:
            raise ValueError(f"Trial {position}: trial_index must be a positive integer")
        if index in seen_indexes:
            raise ValueError(f"Duplicate trial_index {index}")
        seen_indexes.add(index)
        rt = _optional_number(trial.get("reaction_time_ms"), f"Trial {index} reaction_time_ms",
                              minimum=0, maximum=MAX_REACTION_TIME_MS)
        isi = _optional_number(trial.get("interstimulus_interval_ms"),
                               f"Trial {index} interstimulus_interval_ms", strictly_positive=True)
        early = _require_bool(trial.get("responded_before_stimulus", False),
                              f"Trial {index} responded_before_stimulus")
        if early and rt is not None:
            raise ValueError(f"Trial {index}: a response before the stimulus has no reaction time")
        trial_rows.append({
            "assessment_id": assessment_id,
            "trial_index": index,
            "interstimulus_interval_ms": isi,
            "reaction_time_ms": rt,
            "responded_before_stimulus": int(early),
            "outcome": classify_trial(rt, early, lapse_threshold_ms=lapse_ms,
                                      false_start_threshold_ms=false_ms),
        })
    trial_rows.sort(key=lambda r: r["trial_index"])

    row = {
        "assessment_id": assessment_id,
        "astronaut_id": astronaut_id,
        "mission_id": mission_id,
        "assessment_utc": timestamp,
        "protocol_version": protocol_version,
        "timing_method": timing_method,
        "completion_status": completion_status,
        "data_origin": data_origin,
        "is_synthetic": int(is_synthetic),
        "lapse_threshold_ms": lapse_ms,
        "false_start_threshold_ms": false_ms,
        **summarize_trials(trial_rows),
        "self_reported_fatigue": fatigue,
        "notes": _optional_text(notes, "notes"),
    }

    _require_astronaut_and_mission(conn, astronaut_id, mission_id)
    stored, inserted = _insert_or_verify(
        conn, "alertness_assessments", row, ["assessment_id"],
        ["astronaut_id", "assessment_utc"],
    )
    stored_id = stored["assessment_id"]
    if not inserted:
        stored_trials = [dict(r) for r in conn.execute(
            "SELECT * FROM reaction_time_trials WHERE assessment_id = ? ORDER BY trial_index",
            (stored_id,),
        )]
        expected = [{**t, "assessment_id": stored_id} for t in trial_rows]
        if stored_trials != expected:
            raise ValueError(f"Conflicting reaction-time trials for assessment {stored_id}")
        return stored_id, False

    for trial_row in trial_rows:
        _insert_or_verify(conn, "reaction_time_trials", trial_row,
                          ["assessment_id", "trial_index"])
    return stored_id, True


def add_alertness_assessment(astronaut_id: str, mission_id: str, assessment_utc,
                             trials: Sequence[Mapping], *, protocol_version: str,
                             timing_method: str, data_origin: str = "app",
                             is_synthetic: bool = False, completion_status: str = "completed",
                             lapse_threshold_ms: float = DEFAULT_LAPSE_THRESHOLD_MS,
                             false_start_threshold_ms: float = DEFAULT_FALSE_START_THRESHOLD_MS,
                             self_reported_fatigue: int | None = None, notes: str | None = None,
                             assessment_id: str | None = None, db_path=None) -> str:
    """Store one reaction-time assessment with its individual trials.

    ``trials`` are mappings with optional ``trial_index`` (defaults to list
    position), ``interstimulus_interval_ms``, ``reaction_time_ms`` (None = no
    response) and ``responded_before_stimulus``. The backend labels each trial
    and computes the summary statistics; callers cannot supply summaries, so
    stored summaries always match stored trials. Atomic. Returns the ID.
    """
    with closing(_connect(db_path)) as conn, conn:
        stored_id, _ = _add_alertness_assessment(
            conn, astronaut_id=astronaut_id, mission_id=mission_id,
            assessment_utc=assessment_utc, trials=trials,
            protocol_version=protocol_version, timing_method=timing_method,
            data_origin=data_origin, is_synthetic=is_synthetic,
            completion_status=completion_status,
            lapse_threshold_ms=lapse_threshold_ms,
            false_start_threshold_ms=false_start_threshold_ms,
            self_reported_fatigue=self_reported_fatigue, notes=notes,
            assessment_id=assessment_id,
        )
    return stored_id


# ---------------------------------------------------------------------------
# Cortisol measurements (optional)
# ---------------------------------------------------------------------------

def _add_cortisol_measurement(conn, *, astronaut_id, mission_id, sample_utc, value, unit,
                              sampling_method, data_origin="manual", is_synthetic=False,
                              minutes_since_awakening=None, sleep_session_id=None,
                              notes=None, cortisol_id=None) -> tuple[str, bool]:
    astronaut_id = _require_id(astronaut_id, "astronaut_id")
    mission_id = _require_id(mission_id, "mission_id")
    cortisol_id = (
        f"CRT-{uuid4().hex}" if cortisol_id is None else _require_id(cortisol_id, "cortisol_id")
    )
    sample = normalize_utc(sample_utc, "sample_utc")
    measured = _optional_number(value, "value", strictly_positive=True)
    if measured is None:
        raise ValueError("Cortisol value is required")
    _require_choice(unit, "unit", CORTISOL_UNITS)
    _require_choice(sampling_method, "sampling_method", CORTISOL_SAMPLING_METHODS)
    _require_choice(data_origin, "data_origin", CORTISOL_DATA_ORIGINS)
    _require_bool(is_synthetic, "is_synthetic")
    if data_origin == "synthetic" and not is_synthetic:
        raise ValueError("data_origin 'synthetic' requires is_synthetic=True")
    since_wake = _optional_number(minutes_since_awakening, "minutes_since_awakening", minimum=0)

    _require_astronaut_and_mission(conn, astronaut_id, mission_id)

    if sleep_session_id is not None:
        sleep_session_id = _require_id(sleep_session_id, "sleep_session_id")
        session = conn.execute(
            "SELECT astronaut_id, mission_id, wake_time_utc FROM sleep_sessions "
            "WHERE sleep_session_id = ?", (sleep_session_id,),
        ).fetchone()
        if session is None:
            raise ValueError(f"Unknown sleep session: {sleep_session_id!r}")
        if (session["astronaut_id"], session["mission_id"]) != (astronaut_id, mission_id):
            raise ValueError("Linked sleep session belongs to a different astronaut or mission")
        derived = _minutes_between(session["wake_time_utc"], sample)
        if derived < 0:
            raise ValueError("Cortisol sample precedes the linked session's wake time")
        if since_wake is None:
            since_wake = round(derived, 1)
        elif abs(since_wake - derived) > AWAKENING_TOLERANCE_MINUTES:
            raise ValueError("minutes_since_awakening conflicts with the linked session's wake time")

    row = {
        "cortisol_id": cortisol_id,
        "astronaut_id": astronaut_id,
        "mission_id": mission_id,
        "sample_utc": sample,
        "value": measured,
        "unit": unit,
        "sampling_method": sampling_method,
        "minutes_since_awakening": since_wake,
        "sleep_session_id": sleep_session_id,
        "data_origin": data_origin,
        "is_synthetic": int(is_synthetic),
        "notes": _optional_text(notes, "notes"),
    }
    stored, inserted = _insert_or_verify(
        conn, "cortisol_measurements", row, ["cortisol_id"],
        ["astronaut_id", "sample_utc", "sampling_method"],
    )
    return stored["cortisol_id"], inserted


def add_cortisol_measurement(astronaut_id: str, mission_id: str, sample_utc, value: float,
                             unit: str, sampling_method: str, *, data_origin: str = "manual",
                             is_synthetic: bool = False,
                             minutes_since_awakening: float | None = None,
                             sleep_session_id: str | None = None, notes: str | None = None,
                             cortisol_id: str | None = None, db_path=None) -> str:
    """Store one optional cortisol observation and return its ID.

    The unit is stored exactly as given (no conversion). If
    ``sleep_session_id`` is given, ``minutes_since_awakening`` is derived from
    that session's wake time (or checked against it). Cortisol is contextual
    data only and must never be used as a standalone alertness indicator.
    """
    with closing(_connect(db_path)) as conn, conn:
        stored_id, _ = _add_cortisol_measurement(
            conn, astronaut_id=astronaut_id, mission_id=mission_id, sample_utc=sample_utc,
            value=value, unit=unit, sampling_method=sampling_method,
            data_origin=data_origin, is_synthetic=is_synthetic,
            minutes_since_awakening=minutes_since_awakening,
            sleep_session_id=sleep_session_id, notes=notes, cortisol_id=cortisol_id,
        )
    return stored_id


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

_BOOL_COLUMNS = ("is_synthetic", "is_estimated", "responded_before_stimulus")


def _to_dict(row) -> dict:
    record = dict(row)
    for column in _BOOL_COLUMNS:
        if column in record:
            record[column] = bool(record[column])
    return record


def _window(start_utc, end_utc) -> tuple[str | None, str | None]:
    start = None if start_utc is None else normalize_utc(start_utc, "start_utc", allow_future=True)
    end = None if end_utc is None else normalize_utc(end_utc, "end_utc", allow_future=True)
    if start is not None and end is not None and end <= start:
        raise ValueError("end_utc must be later than start_utc")
    return start, end


def _scoped_query(table, astronaut_id, mission_id):
    astronaut_id = _require_id(astronaut_id, "astronaut_id")
    query = f"SELECT * FROM {table} WHERE astronaut_id = ?"
    params: list = [astronaut_id]
    if mission_id is not None:
        query += " AND mission_id = ?"
        params.append(_require_id(mission_id, "mission_id"))
    return query, params


def get_sleep_stages(sleep_session_id: str, *, db_path=None) -> list[dict]:
    """Estimated stages for one session, chronological. ``[]`` if unavailable."""
    sleep_session_id = _require_id(sleep_session_id, "sleep_session_id")
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM sleep_stages WHERE sleep_session_id = ? ORDER BY start_utc",
            (sleep_session_id,),
        ).fetchall()
    return [_to_dict(r) for r in rows]


def get_sleep_sessions(astronaut_id: str, *, mission_id: str | None = None,
                       start_utc=None, end_utc=None, data_origin: str | None = None,
                       sleep_type: str | None = None, include_stages: bool = False,
                       db_path=None) -> list[dict]:
    """Sleep sessions for one astronaut, ordered by bedtime ascending.

    ``start_utc``/``end_utc`` select sessions whose time-in-bed interval
    overlaps ``[start_utc, end_utc)``. Each record adds a derived
    ``time_in_bed_minutes``. With ``include_stages=True`` each record also
    has ``sleep_stages`` (possibly empty) and ``stage_data_status``
    (``"available"`` or ``"unavailable"``).
    """
    query, params = _scoped_query("sleep_sessions", astronaut_id, mission_id)
    start, end = _window(start_utc, end_utc)
    if start is not None:
        query += " AND wake_time_utc > ?"
        params.append(start)
    if end is not None:
        query += " AND bedtime_utc < ?"
        params.append(end)
    if data_origin is not None:
        query += " AND data_origin = ?"
        params.append(_require_choice(data_origin, "data_origin", SLEEP_DATA_ORIGINS))
    if sleep_type is not None:
        query += " AND sleep_type = ?"
        params.append(_require_choice(sleep_type, "sleep_type", SLEEP_TYPES))
    query += " ORDER BY bedtime_utc, sleep_session_id"

    with closing(_connect(db_path)) as conn:
        sessions = [_to_dict(r) for r in conn.execute(query, params)]
        for session in sessions:
            session["time_in_bed_minutes"] = _minutes_between(
                session["bedtime_utc"], session["wake_time_utc"]
            )
            if include_stages:
                stages = [_to_dict(r) for r in conn.execute(
                    "SELECT * FROM sleep_stages WHERE sleep_session_id = ? ORDER BY start_utc",
                    (session["sleep_session_id"],),
                )]
                session["sleep_stages"] = stages
                session["stage_data_status"] = "available" if stages else "unavailable"
    return sessions


def get_reaction_time_trials(assessment_id: str, *, db_path=None) -> list[dict]:
    """Individual trials for one assessment, ordered by ``trial_index``."""
    assessment_id = _require_id(assessment_id, "assessment_id")
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM reaction_time_trials WHERE assessment_id = ? ORDER BY trial_index",
            (assessment_id,),
        ).fetchall()
    return [_to_dict(r) for r in rows]


def get_alertness_assessments(astronaut_id: str, *, mission_id: str | None = None,
                              start_utc=None, end_utc=None, include_trials: bool = False,
                              db_path=None) -> list[dict]:
    """Assessments in ``[start_utc, end_utc)``, ordered by time ascending.

    With ``include_trials=True`` each record also has ``trials``.
    """
    query, params = _scoped_query("alertness_assessments", astronaut_id, mission_id)
    start, end = _window(start_utc, end_utc)
    if start is not None:
        query += " AND assessment_utc >= ?"
        params.append(start)
    if end is not None:
        query += " AND assessment_utc < ?"
        params.append(end)
    query += " ORDER BY assessment_utc, assessment_id"

    with closing(_connect(db_path)) as conn:
        assessments = [_to_dict(r) for r in conn.execute(query, params)]
        if include_trials:
            for assessment in assessments:
                assessment["trials"] = [_to_dict(r) for r in conn.execute(
                    "SELECT * FROM reaction_time_trials WHERE assessment_id = ? "
                    "ORDER BY trial_index", (assessment["assessment_id"],),
                )]
    return assessments


def get_cortisol_measurements(astronaut_id: str, *, mission_id: str | None = None,
                              start_utc=None, end_utc=None, db_path=None) -> list[dict]:
    """Cortisol observations in ``[start_utc, end_utc)``, ordered by time."""
    query, params = _scoped_query("cortisol_measurements", astronaut_id, mission_id)
    start, end = _window(start_utc, end_utc)
    if start is not None:
        query += " AND sample_utc >= ?"
        params.append(start)
    if end is not None:
        query += " AND sample_utc < ?"
        params.append(end)
    query += " ORDER BY sample_utc, cortisol_id"
    with closing(_connect(db_path)) as conn:
        return [_to_dict(r) for r in conn.execute(query, params)]


__all__ = [
    "normalize_utc",
    "classify_trial",
    "summarize_trials",
    "add_sleep_session",
    "add_sleep_stages",
    "add_alertness_assessment",
    "add_cortisol_measurement",
    "get_sleep_sessions",
    "get_sleep_stages",
    "get_alertness_assessments",
    "get_reaction_time_trials",
    "get_cortisol_measurements",
]
