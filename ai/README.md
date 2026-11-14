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

## Backend entry point and schema

```python
import pandas as pd
from ai import analyze_astronaut_data

data = pd.read_csv("ai/data/simulated_health.csv")
report = analyze_astronaut_data(
    history=data[data.date < "2026-01-08"],
    measurements=data[data.date >= "2026-01-08"],
)
```

Both inputs accept Pandas DataFrames or lists of dictionaries. Required columns:

| Column | Meaning |
| --- | --- |
| astronaut_id | Nonempty string; stable pseudonymous identifier |
| date | Calendar day, preferably ISO `YYYY-MM-DD`; parsed at UTC midnight |
| sleep_hours | Daily sleep duration in hours |
| resting_heart_rate | Daily resting heart rate in beats/minute |
| activity_minutes | Daily activity duration in minutes |
| stress_level | Nonnegative numeric score on a consistent upstream scale |

One row per astronaut per day; no automatic duplicate aggregation. Numeric
measurements may be missing (`None`, NaN, or blank CSV cell). Negative, infinite,
and nonnumeric measurements, invalid IDs/dates, duplicate days, or missing columns
raise ValueError. Extra columns are ignored. Inputs are copied, sorted internally,
and never modified. Empty inputs must still declare the schema (empty DataFrame
with the required columns). Stress-scale calibration and physiological plausibility
validation belong to the data producer; this module invents no clinical ranges.

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

## Output

The return value is a JSON-safe dictionary:

```text
{
  "baselines": {astronaut_id: {metric: {count, mean, std, scale}}},
  "results": [{
    "astronaut_id": "SIM-A", "date": "2026-01-10",
    "alert_level": "warning",
    "anomalies": [{"metric": "sleep_hours", "value": 7.4,
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
