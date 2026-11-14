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
