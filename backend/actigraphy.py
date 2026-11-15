"""Actiwatch-compatible actigraphy import and synthetic Actiwatch-like data.

Reference device family: Philips/Respironics Actiwatch Spectrum (wrist-worn
actigraphy). Actigraphy records wrist MOVEMENT (activity counts per epoch),
ambient LIGHT, off-wrist status and event markers. Sleep/wake is ESTIMATED
from movement. Actigraphy does not measure REM/deep sleep, EEG, cortisol,
reaction time or heart-rate variability, and AstroGuard does not assume the
watch can stream data, beep, vibrate or receive commands.

Supported input: the "Actiware Export File" CSV (verified against Version
05.00 exports in the public "Chronic insomnia and bed partner actigraphy data"
dataset, Zenodo record 4961281, CC0). The parser reads the
"Epoch-by-Epoch Data" section with columns such as ``Date``, ``Time``,
``Off-Wrist Status``, ``Activity``, ``Marker``, ``White Light``,
``Sleep/Wake`` (0 = sleep, 1 = wake) and ``Interval Status``
(ACTIVE / REST / REST-S / EXCLUDED). Only Date, Time and Activity are
required; every other column is optional. ``NaN`` cells become NULL.
Personal header fields (name, address, gender, birth date, file paths, serial
numbers) are never stored.

Sleep periods are derived from the export's REST/REST-S intervals when
present (estimated sleep = epochs the device scored as sleep). Otherwise
AstroGuard's own Actiware-STYLE heuristic is used (weighted activity window,
after published descriptions of the Actiware algorithm; not the vendor
implementation and not validated here).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from backend import sleep_alertness as sa
from backend.database import get_connection

ZENODO_ATTRIBUTION = (
    "Angelova M., Kusmakar S., Karmakar C., Zhu Y., Shelyag S., Drummond S., Ellis J. "
    "(2021). 'Chronic insomnia and bed partner actigraphy data'. Zenodo record 4961281, "
    "doi:10.5061/dryad.b8gtht7bh, CC0 1.0. Respironics Actiwatch Spectrum Pro, 60-s epochs; "
    "research participants on Earth (people with chronic insomnia and their bed partners), "
    "not astronauts."
)
ZENODO_URL = "https://zenodo.org/records/4961281"
ZENODO_DOI_URL = "https://doi.org/10.5061/dryad.b8gtht7bh"
ZENODO_TITLE = "Chronic insomnia and bed partner actigraphy data"
RESEARCH_MISSION_ID = "RESEARCH-ACTIWARE-EARTH"
RESEARCH_MISSION_NAME = "Public Actiwatch research data (Earth participants, not a mission)"

# Weighted-window coefficients used by the Actiware-style heuristic.
SCORING_WEIGHTS = {
    60: [0.04, 0.2, 1.0, 0.2, 0.04],
    30: [0.04, 0.04, 0.2, 0.2, 2.0, 0.2, 0.2, 0.04, 0.04],
}
WAKE_THRESHOLDS = {"low": 20.0, "medium": 40.0, "high": 80.0}
MIN_REST_MINUTES = 30
MAIN_SLEEP_MINUTES = 180

_STATUS = {"ACTIVE": "active", "REST": "rest", "REST-S": "rest_sleep", "EXCLUDED": "excluded"}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _cell(value):
    value = (value or "").strip()
    return None if value == "" or value.lower() == "nan" else value


def _number(value, field, line):
    text = _cell(value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        raise ValueError(f"Line {line}: {field} is not numeric: {value!r}") from None
    if not np.isfinite(number) or number < 0:
        raise ValueError(f"Line {line}: {field} must be a finite non-negative number")
    return number


def _flag(value, field, line):
    number = _number(value, field, line)
    if number is None:
        return None
    if number not in (0, 1):
        raise ValueError(f"Line {line}: {field} must be 0 or 1")
    return int(number)


def _parse_offset(text: str) -> timezone:
    match = re.fullmatch(r"([+-])(\d{1,2}):(\d{2})", text.strip())
    if not match:
        raise ValueError(f"Unrecognized time zone offset: {text!r}")
    sign = 1 if match.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3))))


def _date_order(dates: list[str]) -> str:
    firsts = {int(d.split("/")[0]) for d in dates if d.count("/") == 2}
    seconds = {int(d.split("/")[1]) for d in dates if d.count("/") == 2}
    if any(v > 12 for v in firsts):
        return "dmy"
    if any(v > 12 for v in seconds):
        return "mdy"
    raise ValueError("Ambiguous date order (day/month vs month/day); pass date_order explicitly")


def _parse_local(date_text: str, time_text: str, order: str, tz: timezone) -> datetime:
    day_fmt = "%d/%m/%Y" if order == "dmy" else "%m/%d/%Y"
    for time_fmt in ("%I:%M:%S %p", "%H:%M:%S"):
        try:
            local = datetime.strptime(f"{date_text} {time_text}", f"{day_fmt} {time_fmt}")
            return local.replace(tzinfo=tz).astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date/time: {date_text!r} {time_text!r}")


def parse_actiware_csv(source, *, date_order: str = "auto", utc_offset: str | None = None,
                       source_name: str | None = None) -> dict:
    """Parse an Actiware export (path, bytes or text) into metadata + epoch rows.

    Returns ``{"metadata": {...}, "epochs": [ {epoch_start_utc, activity_count,
    white_light_lux, off_wrist, event_marker, device_sleep_wake, interval_status} ]}``.
    """
    if isinstance(source, (str, Path)) and Path(source).exists():
        raw = Path(source).read_bytes()
        name = Path(source).name
    elif isinstance(source, bytes):
        raw, name = source, "uploaded.csv"
    elif isinstance(source, str):
        raw, name = source.encode("utf-8"), "uploaded.csv"
    else:
        raise ValueError("source must be a file path, bytes or CSV text")
    name = source_name or name
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    rows = list(csv.reader(io.StringIO(text)))

    metadata: dict[str, str] = {}
    epoch_section = None
    for index, row in enumerate(rows):
        if row and "Epoch-by-Epoch Data" in row[0]:
            epoch_section = index
            break
        if len(row) >= 2 and row[0].strip().endswith(":") and row[0].strip() not in metadata:
            metadata[row[0].strip()] = row[1].strip()
    header_index = None
    start = epoch_section if epoch_section is not None else 0
    for index in range(start, len(rows)):
        cells = [c.strip() for c in rows[index]]
        if cells and cells[0] == "Line" and "Activity" in cells:
            header_index = index
            break
    if header_index is None:
        raise ValueError("No epoch-by-epoch table with an 'Activity' column was found")
    header = [c.strip() for c in rows[header_index]]
    column = {name_: i for i, name_ in enumerate(header) if name_}
    for required in ("Date", "Time", "Activity"):
        if required not in column:
            raise ValueError(f"Epoch table is missing the required column {required!r}")

    epoch_text = metadata.get("Epoch Length:")
    if epoch_text is None:
        raise ValueError("Export header has no 'Epoch Length:' field")
    epoch_seconds = int(float(epoch_text))
    offset_text = metadata.get("Time Zone Offset:") or utc_offset
    if offset_text is None:
        raise ValueError("Export header has no 'Time Zone Offset:'; pass utc_offset explicitly")
    tz = _parse_offset(offset_text)

    data_rows = [r for r in rows[header_index + 1:]
                 if len(r) > column["Activity"] and _cell(r[column["Date"]])]
    if not data_rows:
        raise ValueError("The epoch table contains no data rows")
    order = date_order if date_order != "auto" else _date_order(
        [r[column["Date"]].strip() for r in data_rows])

    def get(row, name_):
        return row[column[name_]] if name_ in column and len(row) > column[name_] else None

    epochs, seen = [], set()
    for line, row in enumerate(data_rows, start=header_index + 2):
        moment = _parse_local(row[column["Date"]].strip(), row[column["Time"]].strip(), order, tz)
        stamp = moment.strftime(sa.UTC_FORMAT)
        if stamp in seen:
            raise ValueError(f"Line {line}: duplicate epoch timestamp {stamp}")
        seen.add(stamp)
        sleep_wake = _flag(get(row, "Sleep/Wake"), "Sleep/Wake", line)
        status = _cell(get(row, "Interval Status"))
        epochs.append({
            "epoch_start_utc": stamp,
            "activity_count": _number(row[column["Activity"]], "Activity", line),
            "white_light_lux": _number(get(row, "White Light"), "White Light", line),
            "off_wrist": _flag(get(row, "Off-Wrist Status"), "Off-Wrist Status", line),
            "event_marker": _flag(get(row, "Marker"), "Marker", line),
            "device_sleep_wake": None if sleep_wake is None else ("sleep" if sleep_wake == 0
                                                                   else "wake"),
            "interval_status": _STATUS.get(status.upper()) if status else None,
        })
    epochs.sort(key=lambda e: e["epoch_start_utc"])
    identity = (metadata.get("Identity:") or "").split()
    return {
        "metadata": {
            "source_name": name,
            "export_version": (rows[0][0].strip() if rows and rows[0] else None),
            "subject_code": identity[0] if identity else None,
            "epoch_seconds": epoch_seconds,
            "utc_offset": offset_text,
            "wake_threshold": metadata.get("Wake Threshold Value:"),
            "columns": [c for c in header if c],
        },
        "epochs": epochs,
    }


# ---------------------------------------------------------------------------
# Scoring and sleep-period derivation
# ---------------------------------------------------------------------------

def score_sleep_wake(activity, epoch_seconds: int = 60, threshold: float = 40.0) -> list:
    """Actiware-style heuristic: 'wake' if the weighted activity window > threshold.

    Epochs with missing activity are scored ``None``. Illustrative only.
    """
    if epoch_seconds not in SCORING_WEIGHTS:
        raise ValueError(f"Heuristic scoring supports {sorted(SCORING_WEIGHTS)} s epochs")
    weights = SCORING_WEIGHTS[epoch_seconds]
    half = len(weights) // 2
    values = [None if a is None else float(a) for a in activity]
    scores = []
    for i, value in enumerate(values):
        if value is None:
            scores.append(None)
            continue
        total = 0.0
        for k, w in enumerate(weights):
            j = i + k - half
            if 0 <= j < len(values) and values[j] is not None:
                total += w * values[j]
        scores.append("wake" if total > threshold else "sleep")
    return scores


def _quality(coverage: float) -> str:
    if coverage >= 0.95:
        return "good"
    if coverage >= 0.80:
        return "fair"
    if coverage >= 0.50:
        return "poor"
    return "unusable"


def derive_sleep_periods(epochs: list[dict], epoch_seconds: int, *,
                         threshold: float = 40.0) -> list[dict]:
    """Derive sleep periods (time-in-bed intervals + estimated sleep) from epochs."""
    if not epochs:
        return []
    step = timedelta(seconds=epoch_seconds)
    has_intervals = any(e["interval_status"] in ("rest", "rest_sleep") for e in epochs)
    own_scores = (score_sleep_wake([e["activity_count"] for e in epochs], epoch_seconds,
                                   threshold) if epoch_seconds in SCORING_WEIGHTS
                  else [None] * len(epochs))
    if has_intervals:
        in_rest = [e["interval_status"] in ("rest", "rest_sleep") for e in epochs]
        method = "device REST intervals and device Sleep/Wake scores"
    else:
        # Sleep bouts from the heuristic, bridging wake gaps of up to 30 minutes.
        asleep = [s == "sleep" and (e["off_wrist"] != 1) for s, e in zip(own_scores, epochs)]
        gap = int(30 * 60 / epoch_seconds)
        in_rest = list(asleep)
        last = None
        for i, flag in enumerate(asleep):
            if flag:
                if last is not None and 0 < i - last - 1 <= gap:
                    for j in range(last + 1, i):
                        in_rest[j] = True
                last = i
        method = "AstroGuard Actiware-style heuristic (illustrative)"

    periods, i = [], 0
    times = [datetime.strptime(e["epoch_start_utc"], sa.UTC_FORMAT).replace(tzinfo=timezone.utc)
             for e in epochs]
    while i < len(epochs):
        if not in_rest[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(epochs) and in_rest[j + 1] and times[j + 1] - times[j] == step:
            j += 1
        span = epochs[i:j + 1]
        minutes = len(span) * epoch_seconds / 60
        if minutes >= MIN_REST_MINUTES:
            valid = [e for e in span if e["activity_count"] is not None and e["off_wrist"] != 1]
            coverage = len(valid) / len(span)
            quality = _quality(coverage)
            scores = [e["device_sleep_wake"] or own_scores[i + k] for k, e in enumerate(span)]
            asleep_minutes = sum(1 for s, e in zip(scores, span)
                                 if s == "sleep" and e["off_wrist"] != 1) * epoch_seconds / 60
            periods.append({
                "bedtime_utc": span[0]["epoch_start_utc"],
                "wake_time_utc": (times[j] + step).strftime(sa.UTC_FORMAT),
                "time_in_bed_minutes": minutes,
                "estimated_sleep_minutes": None if quality == "unusable" else asleep_minutes,
                "sensor_quality": quality,
                "sensor_coverage_fraction": round(coverage, 3),
                "sleep_type": "main" if minutes >= MAIN_SLEEP_MINUTES else "nap",
                "method": method,
            })
        i = j + 1
    return periods


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _connect(db_path):
    return get_connection() if db_path is None else get_connection(db_path)


def _store_recording(conn, recording: dict, epochs: list[dict]) -> bool:
    """Insert a recording and its epochs; identical re-imports are skipped."""
    row = dict(recording)
    existing = conn.execute("SELECT * FROM actigraphy_recordings WHERE recording_id = ?",
                            (row["recording_id"],)).fetchone()
    if existing is not None:
        # The descriptive attribution text may be corrected between versions; it is not
        # part of the recording's identity. Stored rows are never overwritten.
        same = {k: v for k, v in dict(existing).items() if k != "attribution"} ==                {k: v for k, v in row.items() if k != "attribution"}
        if not same:
            raise ValueError(f"Conflicting actigraphy recording {row['recording_id']}")
        return False
    conn.execute(f"INSERT INTO actigraphy_recordings ({', '.join(row)}) "
                 f"VALUES ({', '.join('?' for _ in row)})", list(row.values()))
    conn.executemany(
        "INSERT INTO actigraphy_epochs (recording_id, epoch_start_utc, activity_count, "
        "white_light_lux, off_wrist, event_marker, device_sleep_wake, interval_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(row["recording_id"], e["epoch_start_utc"], e["activity_count"], e["white_light_lux"],
          e["off_wrist"], e["event_marker"], e["device_sleep_wake"], e["interval_status"])
         for e in epochs],
    )
    return True


def import_actiware_file(source, *, astronaut_id: str | None = None,
                         mission_id: str | None = None, attribution: str | None = None,
                         date_order: str = "auto", utc_offset: str | None = None,
                         source_name: str | None = None, db_path=None) -> dict:
    """Import one real Actiware export and derive wearable sleep sessions.

    Without ``astronaut_id`` the data is stored under a separate RESEARCH
    profile ``RES-<subject code>`` (never a fictional astronaut identity) in
    mission ``RESEARCH-ACTIWARE-EARTH``. Records are labeled real
    (``is_synthetic = 0``) with origin ``actiwatch_export``. One transaction;
    identical re-imports are no-ops.
    """
    parsed = parse_actiware_csv(source, date_order=date_order, utc_offset=utc_offset,
                                source_name=source_name)
    meta, epochs = parsed["metadata"], parsed["epochs"]
    research_profile = astronaut_id is None
    if research_profile:
        code = re.sub(r"[^A-Za-z0-9]", "", meta["subject_code"] or "") or "UNKNOWN"
        astronaut_id = f"RES-{code}"
        mission_id = mission_id or RESEARCH_MISSION_ID
    elif mission_id is None:
        raise ValueError("mission_id is required when importing into an existing astronaut")

    key = f"{astronaut_id}|{meta['source_name']}|{epochs[0]['epoch_start_utc']}"
    recording_id = "ACT-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    recording = {
        "recording_id": recording_id, "astronaut_id": astronaut_id, "mission_id": mission_id,
        "source_format": "actiware_csv", "source_name": meta["source_name"],
        "source_subject_code": meta["subject_code"], "device_model": "Actiwatch (Actiware export)",
        "epoch_seconds": meta["epoch_seconds"], "start_utc": epochs[0]["epoch_start_utc"],
        "end_utc": (datetime.strptime(epochs[-1]["epoch_start_utc"], sa.UTC_FORMAT)
                    + timedelta(seconds=meta["epoch_seconds"])).strftime(sa.UTC_FORMAT),
        "epoch_count": len(epochs), "data_origin": "actiwatch_export", "is_synthetic": 0,
        "attribution": attribution,
    }
    periods = derive_sleep_periods(epochs, meta["epoch_seconds"])
    inserted_sessions = 0
    with closing(_connect(db_path)) as conn, conn:
        if research_profile:
            conn.execute("INSERT OR IGNORE INTO missions (mission_id, mission_name) VALUES (?, ?)",
                         (mission_id, RESEARCH_MISSION_NAME))
            conn.execute("INSERT OR IGNORE INTO astronauts (astronaut_id, display_name) "
                         "VALUES (?, ?)",
                         (astronaut_id, f"Research participant {meta['subject_code']} "
                                        "(Earth dataset, not an astronaut)"))
        sa._require_astronaut_and_mission(conn, astronaut_id, mission_id)
        new_recording = _store_recording(conn, recording, epochs)
        for n, period in enumerate(periods, start=1):
            _, inserted = sa._add_sleep_session(
                conn, sleep_session_id=f"SLP-{recording_id}-{n:03d}",
                astronaut_id=astronaut_id, mission_id=mission_id,
                bedtime_utc=period["bedtime_utc"], wake_time_utc=period["wake_time_utc"],
                data_origin="wearable", is_synthetic=False, sleep_type=period["sleep_type"],
                estimated_sleep_minutes=period["estimated_sleep_minutes"],
                sensor_quality=period["sensor_quality"],
                sensor_coverage_fraction=period["sensor_coverage_fraction"],
                missing_data_notes=(None if period["sensor_quality"] == "good" else
                                    "Off-wrist or missing epochs within the rest interval"),
                notes=f"Derived from Actiware export {meta['source_name']} using "
                      f"{period['method']}",
            )
            inserted_sessions += inserted
    return {"recording_id": recording_id, "astronaut_id": astronaut_id, "mission_id": mission_id,
            "epochs": len(epochs), "new_recording": new_recording,
            "sleep_periods": len(periods), "new_sleep_sessions": inserted_sessions,
            "metadata": meta}


def get_recordings(astronaut_id: str, *, mission_id: str | None = None, db_path=None) -> list[dict]:
    query = "SELECT * FROM actigraphy_recordings WHERE astronaut_id = ?"
    params: list = [astronaut_id]
    if mission_id is not None:
        query += " AND mission_id = ?"
        params.append(mission_id)
    with closing(_connect(db_path)) as conn:
        rows = [dict(r) for r in conn.execute(query + " ORDER BY start_utc", params)]
    for r in rows:
        r["is_synthetic"] = bool(r["is_synthetic"])
    return rows


def recording_summary(recording_id: str, *, db_path=None) -> dict:
    """Data-completeness counts for one recording (for provenance displays)."""
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS epochs,
                      SUM(activity_count IS NULL) AS missing_activity,
                      SUM(off_wrist = 1) AS off_wrist,
                      SUM(white_light_lux IS NOT NULL) AS light_epochs,
                      SUM(device_sleep_wake IS NOT NULL) AS device_scored,
                      SUM(event_marker = 1) AS markers
               FROM actigraphy_epochs WHERE recording_id = ?""", (recording_id,)).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}


def get_epochs(astronaut_id: str, mission_id: str, start_utc: str, end_utc: str, *,
               db_path=None) -> list[dict]:
    """Epochs in ``[start_utc, end_utc)`` for one astronaut and mission."""
    start = sa.normalize_utc(start_utc, "start_utc", allow_future=True)
    end = sa.normalize_utc(end_utc, "end_utc", allow_future=True)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT e.*, r.epoch_seconds, r.data_origin, r.is_synthetic
               FROM actigraphy_epochs AS e
               JOIN actigraphy_recordings AS r ON r.recording_id = e.recording_id
               WHERE r.astronaut_id = ? AND r.mission_id = ?
                 AND e.epoch_start_utc >= ? AND e.epoch_start_utc < ?
               ORDER BY e.epoch_start_utc""",
            (astronaut_id, mission_id, start, end),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Synthetic Actiwatch-like data for the fictional demo crew
# ---------------------------------------------------------------------------

SYNTHETIC_SEED = 20261002


def generate_synthetic_epochs(sessions: list[dict], stages: list[dict], start: datetime,
                              end: datetime, rng) -> list[dict]:
    """Fictional 60-s epochs consistent with stored synthetic sleep sessions.

    Measurement model (assumed, illustrative): awake & out of bed -> activity
    ~ Gamma(mean 250 counts), light 80-450 lux; in bed and asleep -> mostly 0
    with occasional small movements, light < 3 lux; in bed and awake (stage
    'awake' or no stage data) -> moderate activity. Wearable sessions with
    reduced sensor coverage get off-wrist epochs; nights recorded only by a
    manual log are off-wrist (watch not worn).
    """
    minutes = int((end - start).total_seconds() // 60)
    grid = [start + timedelta(minutes=m) for m in range(minutes)]
    index = {t.strftime(sa.UTC_FORMAT): i for i, t in enumerate(grid)}
    state = np.zeros(minutes, dtype=np.int8)        # 0 awake-out, 1 asleep, 2 in-bed awake, 3 off-wrist

    def span(a, b):
        ia = index.get(a, 0 if a < grid[0].strftime(sa.UTC_FORMAT) else None)
        ib = index.get(b, minutes if b >= grid[-1].strftime(sa.UTC_FORMAT) else None)
        return (ia or 0), (ib if ib is not None else minutes)

    wearable_ids = set()
    for s in sessions:
        a, b = span(s["bedtime_utc"], s["wake_time_utc"])
        if s["data_origin"] == "wearable":
            wearable_ids.add(s["sleep_session_id"])
            state[a:b] = 1
            coverage = s["sensor_coverage_fraction"]
            if coverage is not None and coverage < 0.9:
                n_off = int(round((b - a) * (1 - coverage)))
                start_off = a + int(rng.integers(0, max(1, (b - a) - n_off)))
                state[start_off:start_off + n_off] = 3
    for s in sessions:
        if s["data_origin"] == "manual":
            a, b = span(s["bedtime_utc"], s["wake_time_utc"])
            if not (state[a:b] > 0).any():
                state[a:b] = 3
    for st in stages:
        if st["stage_type"] == "awake" and st["sleep_session_id"] in wearable_ids:
            a, b = span(st["start_utc"], st["end_utc"])
            state[a:b] = np.where(state[a:b] == 1, 2, state[a:b])

    activity = np.where(state == 0, rng.gamma(2.5, 100.0, minutes),
               np.where(state == 2, rng.gamma(1.5, 40.0, minutes),
                        np.where(rng.random(minutes) < 0.08, rng.gamma(1.0, 12.0, minutes), 0.0)))
    hour = np.array([t.hour for t in grid])
    light = np.where(state == 0, rng.uniform(80, 450, minutes) * np.where(
        (hour >= 6) & (hour < 22), 1.0, 0.4), rng.uniform(0, 3, minutes))
    epochs = []
    for i, t in enumerate(grid):
        off = state[i] == 3
        epochs.append({
            "epoch_start_utc": t.strftime(sa.UTC_FORMAT),
            "activity_count": None if off else float(round(activity[i])),
            "white_light_lux": None if off else round(float(light[i]), 1),
            "off_wrist": 1 if off else 0,
            "event_marker": 0,
            "device_sleep_wake": None,
            "interval_status": None,
        })
    return epochs


def import_synthetic_actigraphy(*, mission_id: str = "MISSION-SLEEP-DEMO-01",
                                astronaut_ids=("AST-S01", "AST-S02", "AST-S03", "AST-S04"),
                                db_path=None) -> dict:
    """Create reproducible synthetic Actiwatch-like recordings for the demo crew.

    Idempotent: an existing recording for an astronaut is left untouched.
    Derived sleep sessions are NOT created (the synthetic wearable sessions
    already exist); the epochs support activity/light charts and the
    heuristic cross-check shown in the dashboard.
    """
    created = {}
    with closing(_connect(db_path)) as conn, conn:
        for index, astronaut_id in enumerate(astronaut_ids):
            recording_id = f"ACT-SYN-{astronaut_id}"
            if conn.execute("SELECT 1 FROM actigraphy_recordings WHERE recording_id = ?",
                            (recording_id,)).fetchone():
                created[astronaut_id] = False
                continue
            sessions = [dict(r) for r in conn.execute(
                "SELECT * FROM sleep_sessions WHERE astronaut_id = ? AND mission_id = ? "
                "ORDER BY bedtime_utc", (astronaut_id, mission_id))]
            if not sessions:
                created[astronaut_id] = False
                continue
            stages = [dict(r) for r in conn.execute(
                "SELECT st.* FROM sleep_stages st JOIN sleep_sessions s "
                "ON s.sleep_session_id = st.sleep_session_id "
                "WHERE s.astronaut_id = ? AND s.mission_id = ?", (astronaut_id, mission_id))]
            first = datetime.strptime(sessions[0]["bedtime_utc"], sa.UTC_FORMAT)
            last = max(datetime.strptime(s["wake_time_utc"], sa.UTC_FORMAT) for s in sessions)
            start = (first - timedelta(hours=6)).replace(minute=0, tzinfo=timezone.utc)
            end = (last + timedelta(hours=6)).replace(minute=0, tzinfo=timezone.utc)
            rng = np.random.default_rng([SYNTHETIC_SEED, index])
            epochs = generate_synthetic_epochs(sessions, stages, start, end, rng)
            _store_recording(conn, {
                "recording_id": recording_id, "astronaut_id": astronaut_id,
                "mission_id": mission_id, "source_format": "astroguard_synthetic",
                "source_name": "AstroGuard synthetic Actiwatch-like generator v1",
                "source_subject_code": None, "device_model": "Simulated wrist actigraph",
                "epoch_seconds": 60, "start_utc": epochs[0]["epoch_start_utc"],
                "end_utc": end.strftime(sa.UTC_FORMAT), "epoch_count": len(epochs),
                "data_origin": "synthetic", "is_synthetic": 1,
                "attribution": "Fictional synthetic data; not real astronaut measurements.",
            }, epochs)
            created[astronaut_id] = True
    return created


def import_zenodo_archive(archive_path, *, limit: int = 2, db_path=None) -> list[dict]:
    """Import up to ``limit`` CSVs from the downloaded Zenodo 4961281 zip archive.

    The archive is read in place (not extracted); nothing inside it is executed.
    """
    import zipfile

    results = []
    with zipfile.ZipFile(archive_path) as archive:
        names = sorted(n for n in archive.namelist()
                       if n.lower().endswith(".csv") and "/Data/" in n
                       and not n.startswith("__MACOSX"))
        for name in names[:limit]:
            parsed_name = name.rsplit("/", 1)[-1]
            data = archive.read(name)
            result = import_actiware_file(data, attribution=ZENODO_ATTRIBUTION,
                                          source_name=parsed_name, db_path=db_path)
            result["archive_member"] = parsed_name
            results.append(result)
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Import Actiwatch (Actiware CSV) data")
    parser.add_argument("source", help="Actiware CSV file or the Zenodo 4961281 .zip archive")
    parser.add_argument("--limit", type=int, default=2, help="CSV files to import from a zip")
    args = parser.parse_args()
    if str(args.source).lower().endswith(".zip"):
        for item in import_zenodo_archive(args.source, limit=args.limit):
            print(item["archive_member"], "->", item["astronaut_id"], item["sleep_periods"],
                  "sleep periods", "(new)" if item["new_recording"] else "(already imported)")
    else:
        item = import_actiware_file(args.source)
        print(item["astronaut_id"], item["sleep_periods"], "sleep periods")
