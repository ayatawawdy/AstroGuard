# Backend-to-AI integration (Phase 1)

```python
from backend.ai_service import analyze_astronaut_day

report = analyze_astronaut_day(
    "AST-001", "MISSION-DEMO-01", "2026-10-08",
    aggregation="latest",  # Explicitly opt into the selection policy below.
)
```

The service reads the existing SQLite database (or `db_path=` for an isolated
file), selecting one astronaut and one mission. It supports `sleep_duration`,
`heart_rate`, `stress_level`, and optional `activity_minutes`. Other metrics are
ignored. Stored units are checked before conversion to daily AI rows. Timestamps
must include a timezone and are normalized to UTC before assigning calendar days.
Malformed timestamps raise ValueError rather than being silently discarded.

History contains all available daily records strictly before the target day.
The target day is passed only as evaluation data. Later days and measurements
later than the call time are excluded. Future target days are rejected. There is
no CRUD row limit, imputation, interpolation, or zero filling. Missing metrics
become null; even a wholly missing target day returns an insufficient-data result.
Unknown astronaut/mission IDs and invalid arguments raise ValueError.

## Multiple daily readings

The default `aggregation="reject"` raises ValueError if a supported metric has
multiple readings on a UTC day, in either history or the target day. To handle
such records, callers may explicitly select `aggregation="latest"`. That policy
selects the latest absolute timestamp for each metric/day; equal timestamps use
the lexicographically greatest measurement ID as a deterministic tie-breaker.
Insertion order does not matter. No mean, sum, rounding, or clinical interpretation
is applied. In particular, selecting the latest sleep record does not calculate
total sleep. The caller must decide whether this policy suits its recording
protocol. Invalid selected values are rejected by existing AI validation; there
is no fallback to an older valid reading.

## Output and limitations

The service returns `analyze_health_data()` output unchanged, including individual
alerts, explanations, recommendations, quality issues, and optional combined
anomalies. It does not write results, change CRUD or database schema, expose REST
endpoints, change thresholds, or infer diagnoses. Install `ai/requirements.txt`.

This entry point evaluates one day per call. Existing AI persistence detection
requires multiple evaluation days in one call; this service cannot establish a
multi-day persistent alert. Earlier days are baseline history, not an evaluation
window. Multi-day orchestration is a future extension. All available prior mission
history is used, which may contain drift or anomalies. Current-day results may be
partial and can change as more records arrive. Retrieval currently scans all
supported rows for the astronaut/mission to handle mixed timestamp encodings
correctly; large datasets may need indexed normalized timestamps. Activity is
accepted if already stored with unit `minutes`; existing CRUD and Data Engineering
metric catalogs do not yet create it. Heart rate is not documented as resting.

Run the full suite from the repository root:

```sh
python -m unittest discover -s tests -v
python -m unittest discover -s backend -v
python data/test_data_pipeline.py
git diff --check
```

## Inclusive date-range analysis

```python
from backend.ai_service import analyze_astronaut_range

report = analyze_astronaut_range(
    "AST-001", "MISSION-DEMO-01", "2026-10-08", "2026-10-12",
    aggregation="latest",
)
```

`start_day` and `end_day` are inclusive ISO `YYYY-MM-DD` UTC calendar dates.
Reversed, malformed, and future ranges raise ValueError. The range service uses
one database read and calls `analyze_health_data()` once with the entire range.
The baseline includes only available records strictly before `start_day`, frozen
for all evaluated days, as required by the existing AI algorithm. Evaluation
records never enter the baseline, even for later days in the range. There is no
look-ahead or per-day restart of persistence. Minimum history and statistical
thresholds remain unchanged.

The AI tracks each metric separately: same-direction scores at least 2 for three
consecutive days trigger `persistent_change`; scores at least 3 independently
trigger `deviation`. Gaps, missing readings, direction changes, and scores below
the persistence threshold reset the run. Multiple rules on one metric still count
as one contributor to a combined anomaly. Persistence starts at the range start;
include earlier evaluation days to capture runs beginning before it.

One result is returned for every requested date, sorted chronologically, including
wholly missing days as insufficient-data results. A range with no database readings
therefore returns missing/insufficient-data results rather than an empty list.
Missing activity remains optional. Empty pre-range history is supported. History
is not expanded within the range: if a baseline lacks seven valid values at the
start, that metric remains unassessable throughout the batch. A one-day range
matches `analyze_astronaut_day()` apart from its runtime analysis timestamp.

Both services share the same UTC normalization, mission/astronaut isolation,
future-reading exclusion, unit checks, and duplicate policies. The original
single-day service remains available with unchanged behavior. No persistence is
stored between calls. Large ranges materialize a result for every date; callers
should choose practical evaluation windows. This range service supersedes the
single-day-only limitation above without changing that entry point.

## Python interface for Streamlit

Import these public functions from `backend.frontend_service`. It re-exports the
existing implementations, so it adds no database or AI processing layer:

```python
get_astronauts(*, db_path=None) -> list[dict]
get_missions(astronaut_id, *, db_path=None) -> list[dict]
get_measurements(astronaut_id, metric_type=None, limit=100, *, mission_id=None, db_path=None) -> list[dict]
analyze_astronaut_day(astronaut_id, mission_id, target_day, *, aggregation="reject", db_path=DB_PATH) -> dict
analyze_astronaut_range(astronaut_id, mission_id, start_day, end_day, *, aggregation="reject", db_path=DB_PATH) -> dict
```

Omit `db_path` for the configured database. Supply the same SQLite file to all
functions when overriding it. Initialize/import the database first using existing
Backend setup tools. Database errors (`sqlite3.Error`) propagate; an uninitialized
database is not treated as an empty dataset. Existing imports from
`backend.services` and `backend.ai_service` continue to work.

Retrieval contracts:

- Astronauts: sorted by `astronaut_id`; keys `astronaut_id`, `display_name` (nullable).
- Missions: sorted by `mission_id`; keys `mission_id`, `mission_name` (nullable).
  Only missions with stored measurements for that astronaut are discoverable.
  There is no assignment table to discover missions before their first reading.
- Measurements: unchanged long-form rows with `measurement_id`, `astronaut_id`,
  `mission_id`, `timestamp_utc`, `metric_type`, `value`, `unit`, `source`,
  `is_synthetic` (SQLite 0/1 integer), and nullable `context`. Duplicates remain raw
  readings for charts. Optional mission and metric filters are applied in SQL
  before limiting. Ordering retains the existing descending stored timestamp text,
  then measurement ID; normalize timestamps in chart code before chronological
  plotting. Mixed offset encodings can affect which records a limited query returns.

Empty retrievals, unknown astronaut IDs in retrievals, and unknown mission filters
return `[]`. Blank/non-string IDs and invalid limits raise ValueError. Limits are
integers 1–1000, default 100; chart callers must account for truncation. Unknown
metric filters return `[]`. No measurement schema or metric catalog is changed.

AI calls return `{"results": [...]}` unchanged. Unknown astronaut/mission IDs,
invalid ISO dates/ranges, future dates, and duplicate readings under `reject`
raise ValueError. Existing IDs with no readings for that pair produce
insufficient-data results. `status` is `success` or `insufficient_data`;
`alert_level` is `normal`, `warning`, or None. Missing data can coexist with a
warning. A successful assessment is not a medical health determination. Optional
activity absence does not make an otherwise complete assessment insufficient.

### Example Streamlit integration

Run from the repository root after installing the existing Frontend dependencies
and `ai/requirements.txt`. This example reads the configured database. Date inputs
use `.isoformat()` because Backend analysis takes strings, not date objects.
The explicit `latest` policy should be chosen only if suitable for the recording
protocol; omitting it rejects duplicate daily readings.

```python
from datetime import datetime, timedelta, timezone
from sqlite3 import Error as DatabaseError

import pandas as pd
import streamlit as st
from backend.frontend_service import (
    get_astronauts, get_missions, get_measurements,
    analyze_astronaut_day, analyze_astronaut_range,
)

try:
    astronauts = get_astronauts()
    if not astronauts:
        st.info("No astronauts available. Import mission data first.")
        st.stop()
    labels = {a["astronaut_id"]: a["display_name"] or a["astronaut_id"] for a in astronauts}
    astronaut_id = st.selectbox("Astronaut", list(labels), format_func=labels.get)

    missions = get_missions(astronaut_id)
    if not missions:
        st.info("No recorded missions for this astronaut.")
        st.stop()
    names = {m["mission_id"]: m["mission_name"] or m["mission_id"] for m in missions}
    mission_id = st.selectbox("Mission", list(names), format_func=names.get)

    # Raw long-form chart data; never pool different units in one chart.
    rows = get_measurements(astronaut_id, "heart_rate", limit=1000, mission_id=mission_id)
    if rows:
        chart = pd.DataFrame(rows)
        chart["timestamp_utc"] = pd.to_datetime(chart["timestamp_utc"], utc=True, format="ISO8601")
        st.line_chart(chart.sort_values("timestamp_utc").set_index("timestamp_utc")[["value"]],
                      y_label="Heart rate (bpm)")
        if len(rows) == 1000:
            st.caption("Chart retrieval reached its 1000-reading limit.")
    else:
        st.info("No heart rate readings for this selection.")

    today = datetime.now(timezone.utc).date()
    start = st.date_input("Start day (UTC)", today - timedelta(days=6), max_value=today)
    end = st.date_input("End day (UTC)", today, max_value=today)
    mode = st.radio("Analysis", ["Date range", "Single day"])
    if mode == "Single day":
        report = analyze_astronaut_day(astronaut_id, mission_id, end.isoformat(), aggregation="latest")
    else:
        report = analyze_astronaut_range(astronaut_id, mission_id, start.isoformat(), end.isoformat(),
                                         aggregation="latest")

    for result in report["results"]:
        st.subheader(result["date"])
        st.write("Assessment status:", result["status"])
        st.write("Alert level:", result["alert_level"] or "Not fully assessable")
        st.caption("Analyzed at: " + result["analyzed_at"])
        st.write(result["explanation"])
        for anomaly in result["detected_anomalies"]:
            st.write(anomaly["metric"], anomaly["current_value"],
                     "Historical mean:", anomaly["baseline_mean"], "Rules:", anomaly["kinds"])
            st.write(anomaly["explanation"])
        for combined in result.get("combined_anomalies", []):
            st.write("Changes occurring together:", ", ".join(combined["metrics"]))
            for contributor in combined["contributors"]:
                st.write(contributor["metric"], contributor["direction"], "personal baseline")
            st.write(combined["explanation"])
        for recommendation in result["recommendations"]:
            st.write("Recommendation:", recommendation)
        for issue in result["data_quality_issues"]:
            st.info(issue)
except ValueError as exc:
    st.error(str(exc))
except DatabaseError:
    st.error("Could not read the mission database. Check Backend setup.")
```

The existing `app.py` is unchanged and still uses its demonstration alert rule.
Frontend developers must wire this interface into the UI to display AI results.
No Streamlit dependency or caching is added to Backend code. If the Frontend caches
results, it must invalidate them when measurements change. The range baseline is
frozen before the start date and persistence begins at that date, as documented
above. AI analysis is not constrained by the chart retrieval limit.
