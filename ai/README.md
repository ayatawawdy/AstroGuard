# AstroGuard AI prototype

Independent Python module for statistical monitoring, with no frontend, API,
database, or external service. This is simulated-data research software, not a
medical device or diagnostic system. No threshold is NASA-approved or clinically
validated. Scikit-learn is intentionally unnecessary for this statistical version.

## Run

From the repository root, with Python 3.10 or newer:

```sh
python -m venv /tmp/astroguard-ai-venv
/tmp/astroguard-ai-venv/bin/python -m pip install -r ai/requirements.txt
/tmp/astroguard-ai-venv/bin/python -m unittest discover -s tests -v
```

Tests use the standard library's unittest runner. No pytest dependency is needed.

Windows PowerShell (from the repository root, no activation required):

```powershell
py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r ai/requirements.txt
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Backend entry point and schema

Use `from ai import analyze_health_data` for the agreed backend contract.
`analyze_astronaut_data` remains available with its existing signature, output structure,
configuration options, and algorithm. The adapter calls it with unchanged default
settings, including **7 valid historical observations per metric**. It validates the agreed stress input scale and adds output formatting, a real
analysis timestamp, provisional status, summaries, and general suggestions.
The earlier `analyze_for_backend` adapter is retained with its previous output
contract; new backend callers should use `analyze_health_data`.
It has no web/API dependencies and accepts the same two inputs:

```python
import json
from ai import analyze_health_data

# payload is a decoded JSON object with history and measurements arrays.
# report = analyze_health_data(payload["history"], payload["measurements"])
# serialized = json.dumps(report, allow_nan=False)
```

Executable CSV example:

```python
import pandas as pd
from ai import analyze_health_data

data = pd.read_csv("ai/data/simulated_health.csv")
report = analyze_health_data(
    history=data[data.date < "2026-01-08"],
    measurements=data[data.date >= "2026-01-08"],
)
```

Both inputs accept Pandas DataFrames or lists of dictionaries. Daily columns:

The schema was verified against `data/data_dictionary.md`, `data/generate_data.py`,
`backend/services.py`, `backend/database.py`, and `backend/import_data.py`.
Heart rate is documented only as heart rate in bpm; resting conditions cannot be
verified and must not be inferred. Sleep is 0–24 hours; heart rate must be positive.
These are technical input constraints, not medical alert thresholds.

The entry points also accept the team's long-form records: `astronaut_id`,
`timestamp_utc`, `metric_type`, `value`, and `unit`. Units must be `hours`, `bpm`,
and `1-10` for sleep, heart rate, and stress respectively. Timestamps must specify
UTC; they are mapped to UTC calendar days. Other team metrics are ignored.
Missing required metric readings become missing values, never zero. Duplicate
astronaut/day/metric readings are rejected: callers must agree daily aggregation
upstream. Select one mission per astronaut and use the same mission in history
and evaluation; mission metadata does not change the existing response contract.

`activity_minutes` is optional and absent/null readings do not make an assessment
incomplete. Supplied activity values retain the existing baseline and anomaly
algorithm; a supplied reading without enough history is still reported as such.
Activity is not currently provided by the team dataset. All other columns below
are required for daily input:


| Column | Meaning |
| --- | --- |
| astronaut_id | Nonempty string; stable pseudonymous identifier |
| date | Calendar day, preferably ISO `YYYY-MM-DD`; parsed at UTC midnight |
| sleep_duration | Daily sleep duration in hours |
| heart_rate | Daily heart rate (resting conditions are not documented) in beats/minute |
| activity_minutes | Optional daily activity duration in minutes |
| stress_level | Self-reported integer from 1 to 10, or null |

One row per astronaut per day; no automatic duplicate aggregation. Numeric
measurements may be missing (`None`, NaN, or blank CSV cell). Negative, infinite,
and nonnumeric measurements, invalid IDs/dates, duplicate days, or missing required columns
raise ValueError. Extra columns are ignored. Inputs are copied, sorted internally,
and never modified. Empty inputs must still declare the schema (empty DataFrame
with the required columns). The new adapter validates stress values in both inputs:
nonmissing values must be numeric and integer-valued from 1 through 10. Integral
floats such as 2.0 are accepted for CSV/DataFrame compatibility; booleans, numeric
strings, fractions, and out-of-scale values raise ValueError. These are input-scale
constraints, not medical thresholds. Missing stress values do not count toward the
seven historical observations. `fatigue_level` is managed separately by the backend
and is neither required nor analyzed; any supplied extra column is ignored.
All analysis entry points validate the same 1–10 stress scale.

For every evaluated astronaut, all history must precede their earliest evaluation
day. Overlap/future history raises ValueError, preventing look-ahead leakage. For
backtesting, call separately for each historical cutoff. Multiple astronauts can
be evaluated together; their histories are never pooled.

## Algorithm and settings

Each metric uses its astronaut's historical mean and sample standard deviation
(ddof=1), with at least `min_history=7` nonmissing daily values per metric. The
baseline stays frozen throughout an evaluation batch. Choose representative,
stable historical data; this version uses all supplied history without weighting.

The signed score is `(measurement - mean) / max(sample_std, scale_floor)`.
Default scale floors are sleep 0.25 hours, heart rate 2 beats/minute, activity
5 minutes, and stress 0.25 score units. These are explicit engineering sensitivity
controls to avoid division by zero and oversensitivity to constant histories.
They are not health cutoffs. Override all four with `scale_floors={...}` when
appropriate for the measurement instruments and stress scale.

A `deviation` has absolute score >= `deviation_threshold=3`. A
`persistent_change` has absolute score >= `persistence_threshold=2` in the same
direction for `persistence_days=3` consecutive calendar days. Missing days,
missing values, direction reversals, or scores below 2 break that metric's run.
Persistence spans only the measurements supplied in a call; include enough
recent evaluation days on subsequent calls. Both high and low deviations count.
Alerts repeat on each qualifying day; downstream consumers can deduplicate.

## Combined anomaly detection

After detecting individual anomalies, the AI checks each astronaut/day result.
Two or more distinct anomalous metrics produce one combined group containing all
contributing metrics, in the existing metric order. Both `deviation` and
`persistent_change` qualify; a metric triggering both counts once. Missing values
and metrics without baselines do not contribute. Optional activity contributes
only when supplied and independently detected as anomalous. Dates and astronauts
are never pooled, and no additional thresholds or severity levels are introduced.

All three entry points optionally add `combined_anomalies` to each result. It is
omitted when fewer than two metrics have detected anomalies. Existing response
fields, individual anomaly structures, summaries, recommendations, statuses, and
alert levels retain their behavior. Consumers should allow this additional field;
strict response schemas must declare it optional before consuming combined alerts.

Example additional field for sleep below baseline and heart rate above baseline:

```json
{
  "combined_anomalies": [
    {
      "metrics": ["sleep_duration", "heart_rate"],
      "contributors": [
        {"metric": "sleep_duration", "direction": "below"},
        {"metric": "heart_rate", "direction": "above"}
      ],
      "explanation": "Detected statistical anomalies occurred together on the same day for this astronaut: sleep_duration below its personal historical mean; heart_rate above its personal historical mean. Co-occurrence does not establish a cause or medical diagnosis."
    }
  ]
}
```

`direction` describes the current value relative to that astronaut's frozen
historical mean. The individual anomaly records retain values, baseline evidence,
and detection kinds. A group describes co-occurrence within a UTC calendar day,
not simultaneous readings, correlation, cause, or clinical significance. There
is no separate combined persistence rule. Existing daily aggregation limitations
and minimum-history requirements still apply.

## Output

### Agreed backend JSON examples

Complete simulated request (the backend passes the two arrays as arguments):

```json
{
  "history": [
    {"astronaut_id": "SIM-A", "date": "2026-01-01", "sleep_duration": 8, "heart_rate": 60, "activity_minutes": 45, "stress_level": 2},
    {"astronaut_id": "SIM-A", "date": "2026-01-02", "sleep_duration": 8, "heart_rate": 60, "activity_minutes": 45, "stress_level": 2},
    {"astronaut_id": "SIM-A", "date": "2026-01-03", "sleep_duration": 8, "heart_rate": 60, "activity_minutes": 45, "stress_level": 2},
    {"astronaut_id": "SIM-A", "date": "2026-01-04", "sleep_duration": 8, "heart_rate": 60, "activity_minutes": 45, "stress_level": 2},
    {"astronaut_id": "SIM-A", "date": "2026-01-05", "sleep_duration": 8, "heart_rate": 60, "activity_minutes": 45, "stress_level": 2},
    {"astronaut_id": "SIM-A", "date": "2026-01-06", "sleep_duration": 8, "heart_rate": 60, "activity_minutes": 45, "stress_level": 2},
    {"astronaut_id": "SIM-A", "date": "2026-01-07", "sleep_duration": 8, "heart_rate": 60, "activity_minutes": 45, "stress_level": 2}
  ],
  "measurements": [
    {"astronaut_id": "SIM-A", "date": "2026-01-08", "sleep_duration": 6, "heart_rate": 60, "activity_minutes": 45, "stress_level": 2}
  ]
}
```

Response shape for that request (`analyzed_at` below is illustrative; the function
generates the actual UTC timestamp at runtime):

```json
{
  "results": [
    {
      "astronaut_id": "SIM-A",
      "date": "2026-01-08",
      "analyzed_at": "2026-11-14T12:00:00.000000+00:00",
      "status": "success",
      "alert_level": "warning",
      "detected_anomalies": [
        {
          "metric": "sleep_duration",
          "current_value": 6.0,
          "baseline_mean": 8.0,
          "kinds": ["deviation"],
          "explanation": "sleep_duration=6 is 8.00 scaled deviations below the historical mean 8.00 (scale 0.25)."
        }
      ],
      "explanation": "Statistical changes detected in: sleep_duration.",
      "recommendations": [
        "Verify the recorded values and measurement consistency for: sleep_duration.",
        "Review subsequent daily measurements for continued statistical changes."
      ],
      "data_quality_issues": []
    }
  ]
}
```

The adapter always returns `{"results": [...]}`, even for one astronaut/day.
There is exactly one result per supplied astronaut/date pair, sorted by astronaut
ID then date. The `date` identifies the measurement day; `analyzed_at` is the actual
timezone-aware ISO 8601 UTC analysis time, shared across all results in one call.
Results are not collapsed to one alert per astronaut or just the latest date.
An empty evaluation DataFrame with the required columns returns `{"results": []}`.
An empty history DataFrame with the required columns produces insufficient-data
results. Schema/validation errors still raise ValueError; the backend decides how
to map these to its API response.

Anomalies contain `metric`, `current_value`, `baseline_mean`, `kinds`, and an
explanation. A detected anomaly always has a baseline; metrics without one are
reported as data-quality issues instead of anomalies. `kinds` may include
`deviation`, `persistent_change`, or both. All output is serializable with
`json.dumps(report, allow_nan=False)`.

For a missing sleep measurement with otherwise normal assessable metrics, the
result has `status: "insufficient_data"`, `alert_level: null`, no detected anomalies, and
`"sleep_duration: missing measurement"` in data_quality_issues. Fewer than seven
valid history values gives `"sleep_duration: insufficient historical values"`.
Both issues are reported when both apply. Missing values use JSON `null` as input.
An anomalous day can remain `warning` while also reporting incomplete assessment;
an incompletely assessed day is never `normal`.

Status and alert behavior (status names are **provisional**, pending confirmation
with the Backend Developer):

| Assessment | Anomalies | status | alert_level |
| --- | --- | --- | --- |
| Complete | None | `success` | `normal` |
| Complete | One or more | `success` | `warning` |
| Incomplete | None | `insufficient_data` | `null` |
| Incomplete | One or more | `insufficient_data` | `warning` |

`success` means every required metric and supplied activity reading could be assessed, even if it triggered an anomaly;
it does not mean healthy. Missing measurements or insufficient history for any
required metric make the day's assessment incomplete. For example, replacing stress with
`null` in the request above retains the sleep anomaly and `warning`, changes status
to `insufficient_data`, and adds `"stress_level: missing measurement"` to
`data_quality_issues`. The summary explicitly states that assessment is incomplete.

Exact incomplete response example: use the same seven history records above and
this evaluation array (otherwise normal, with missing stress):

```json
[
  {"astronaut_id": "SIM-A", "date": "2026-01-08", "sleep_duration": 8, "heart_rate": 60, "activity_minutes": 45, "stress_level": null}
]
```

```json
{
  "results": [
    {
      "astronaut_id": "SIM-A",
      "date": "2026-01-08",
      "analyzed_at": "2026-11-14T12:00:00.000000+00:00",
      "status": "insufficient_data",
      "alert_level": null,
      "detected_anomalies": [],
      "explanation": "The day could not be fully assessed; no anomalies were detected in the assessable metrics. Assessment is incomplete: stress_level: missing measurement.",
      "recommendations": ["Complete or verify missing measurements before reassessing this day."],
      "data_quality_issues": ["stress_level: missing measurement"]
    }
  ]
}
```

Recommendations only suggest checking recordings, reviewing subsequent data or
persistent patterns, and completing missing measurements/history. Normal results
have an empty recommendations list. Suggestions do not prescribe treatment,
infer a cause, or claim clinical significance.

### Statistical analysis API

`analyze_astronaut_data` continues to return this JSON-safe dictionary:

```text
{
  "baselines": {astronaut_id: {metric: {count, mean, std, scale}}},
  "results": [{
    "astronaut_id": "SIM-A", "date": "2026-01-10",
    "alert_level": "warning",
    "anomalies": [{"metric": "sleep_duration", "value": 7.4,
      "score": -2.4, "kinds": ["persistent_change"],
      "consecutive_days": 3, "explanation": "..."}],
    "data_quality_issues": []
  }]
}
```

Each evaluated day produces a result, ordered by astronaut ID then date.
`warning` means at least one statistical anomaly; `insufficient_data` means no
anomaly was found but one or more metrics could not be assessed; `normal` means
all metrics were assessed without triggering a rule. Missing baselines are omitted
and explained in data_quality_issues. A warning can coexist with missing-data
issues; normal never means medically healthy. Explanations name the observed
value, baseline mean, scale, deviation direction, and any persistent run length.

## Simulated data and limitations

`data/simulated_health.csv` is entirely fabricated, with IDs prefixed SIM. It
contains seven baseline days and four evaluation days for two astronauts: a
moderate sustained sleep change and heart-rate spike for SIM-A, and normal
values plus one missing stress value for SIM-B. It is not astronaut or NASA data.

Seven observations are a development minimum, not a validated sample size. Means
and standard deviations are sensitive to outliers, skew, sensor errors, and
contaminated history. These rules do not model circadian patterns, mission phase,
medication, correlated metrics, slow baseline drift, or causal health outcomes.
Calendar dates need consistent upstream daily aggregation. No imputation occurs.
Alert thresholds require evaluation on suitable, consented data and expert review
before any operational use; false alarms and missed changes are expected.
