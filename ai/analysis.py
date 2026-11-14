"""Personalized statistical monitoring; no population health thresholds."""

from numbers import Integral

import numpy as np
import pandas as pd

METRICS = ("sleep_hours", "resting_heart_rate", "activity_minutes", "stress_level")
REQUIRED = ("astronaut_id", "date", *METRICS)
# Numerical sensitivity controls in input units, NOT medical thresholds.
SCALE_FLOORS = dict(zip(METRICS, (0.25, 2.0, 5.0, 0.25)))


def _validate(data, label):
    frame = pd.DataFrame(data).copy()
    missing = set(REQUIRED) - set(frame.columns)
    if missing:
        raise ValueError(f"{label}: missing columns: {sorted(missing)}")
    if frame["astronaut_id"].isna().any() or not frame["astronaut_id"].map(
        lambda value: isinstance(value, str) and bool(value.strip())
    ).all():
        raise ValueError(f"{label}: astronaut_id must be a nonempty string")
    try:
        frame["date"] = pd.to_datetime(frame["date"], errors="raise", utc=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label}: invalid date") from exc
    if frame["date"].isna().any() or (
        frame["date"] != frame["date"].dt.normalize()
    ).any():
        raise ValueError(f"{label}: dates must be nonmissing calendar days at UTC midnight")
    if frame.duplicated(["astronaut_id", "date"]).any():
        raise ValueError(f"{label}: duplicate astronaut/date")
    for metric in METRICS:
        try:
            frame[metric] = pd.to_numeric(frame[metric], errors="raise").astype(float)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{label}: {metric} must be numeric or missing") from exc
        values = frame[metric].dropna()
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f"{label}: {metric} must be finite and nonnegative")
    return frame.sort_values(["astronaut_id", "date"])


def analyze_astronaut_data(history, measurements, *, min_history=7,
                           deviation_threshold=3.0, persistence_threshold=2.0,
                           persistence_days=3, scale_floors=None):
    """Analyze daily records against frozen earlier history; return JSON-safe dict.

    Inputs are DataFrames or lists of row dictionaries. See ai/README.md for
    schema, output, missing-data behavior and the nonclinical threshold policy.
    """
    if any(isinstance(v, bool) or not isinstance(v, Integral) or v < 2
           for v in (min_history, persistence_days)):
        raise ValueError("min_history and persistence_days must be integers >= 2")
    if not (np.isfinite(deviation_threshold) and np.isfinite(persistence_threshold)
            and 0 < persistence_threshold <= deviation_threshold):
        raise ValueError("thresholds must be finite, positive and ordered")
    floors = SCALE_FLOORS if scale_floors is None else scale_floors
    if set(floors) != set(METRICS) or any(
        not np.isfinite(v) or v <= 0 for v in floors.values()
    ):
        raise ValueError("scale_floors must contain a positive finite value per metric")
    history = _validate(history, "history")
    measurements = _validate(measurements, "measurements")
    results, all_baselines = [], {}
    for astronaut_id, rows in measurements.groupby("astronaut_id", sort=True):
        past = history[history["astronaut_id"] == astronaut_id]
        if not past.empty and past["date"].max() >= rows["date"].min():
            raise ValueError(f"{astronaut_id}: history must precede all measurements")
        baselines = {}
        for metric in METRICS:
            values = past[metric].dropna()
            if len(values) >= min_history:
                std = float(values.std(ddof=1))
                baselines[metric] = {"count": len(values), "mean": float(values.mean()),
                                     "std": std, "scale": max(std, float(floors[metric]))}
        all_baselines[astronaut_id] = baselines
        runs = {metric: (None, 0, 0) for metric in METRICS}
        for _, row in rows.iterrows():
            anomalies, issues = [], []
            for metric in METRICS:
                value = row[metric]
                if pd.isna(value) or metric not in baselines:
                    issues.append(f"{metric}: " + ("missing measurement" if pd.isna(value)
                                                   else "insufficient historical values"))
                    runs[metric] = (None, 0, 0)
                    continue
                baseline = baselines[metric]
                score = (float(value) - baseline["mean"]) / baseline["scale"]
                direction = 1 if score > 0 else -1 if score < 0 else 0
                previous, old_direction, count = runs[metric]
                if abs(score) >= persistence_threshold:
                    count = count + 1 if (previous is not None and
                        (row["date"].date() - previous.date()).days == 1 and
                        direction == old_direction) else 1
                    runs[metric] = (row["date"], direction, count)
                else:
                    count = 0
                    runs[metric] = (None, 0, 0)
                unusual = abs(score) >= deviation_threshold
                persistent = count >= persistence_days
                if unusual or persistent:
                    kinds = (["deviation"] if unusual else []) + (["persistent_change"] if persistent else [])
                    explanation = (
                        f"{metric}={value:g} is {abs(score):.2f} scaled deviations "
                        f"{'above' if direction > 0 else 'below'} the historical mean "
                        f"{baseline['mean']:.2f} (scale {baseline['scale']:.2f})."
                    )
                    if persistent:
                        explanation += f" Same-direction change persisted for {count} consecutive days."
                    anomalies.append({"metric": metric, "value": float(value),
                                      "score": float(score), "kinds": kinds,
                                      "consecutive_days": count, "explanation": explanation})
            results.append({"astronaut_id": astronaut_id, "date": row["date"].date().isoformat(),
                            "alert_level": "warning" if anomalies else "normal" if not issues else "insufficient_data",
                            "anomalies": anomalies, "data_quality_issues": issues})
    return {"baselines": all_baselines, "results": results}
