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
