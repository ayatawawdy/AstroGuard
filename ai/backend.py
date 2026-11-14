"""JSON-friendly adapter for the agreed backend contract."""

from datetime import datetime, timezone
from numbers import Real

import numpy as np
import pandas as pd

from .analysis import METRICS, analyze_astronaut_data


def analyze_health_data(history, measurements):
    """Final backend contract; provisional success/insufficient_data statuses.

    Stress is an integer-valued self-report from 1 to 5 or missing. Integral
    floats are accepted for CSV/DataFrame interoperability; booleans and numeric
    strings are rejected. The legacy analysis functions retain their contracts.
    """
    validated = []
    for label, data in (("history", history), ("measurements", measurements)):
        frame = pd.DataFrame(data).copy()
        if "stress_level" not in frame:
            raise ValueError(f"{label}: missing column: stress_level")
        for value in frame["stress_level"]:
            if pd.isna(value):
                continue
            if (isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
                    or not np.isfinite(value) or not 1 <= value <= 5
                    or value != int(value)):
                raise ValueError(f"{label}: stress_level must be an integer from 1 to 5 or null")
        validated.append(frame)
    report = analyze_for_backend(*validated)
    for row in report["results"]:
        row["status"] = "insufficient_data" if row["data_quality_issues"] else "success"
        if row["alert_level"] == "insufficient_data":
            row["alert_level"] = None
    return report


def analyze_for_backend(history, measurements):
    """Return one result per astronaut/day using the existing default algorithm.

    All records share the actual UTC analysis timestamp for this call. The date
    field identifies the measurement day, independently of analyzed_at. At least
    seven valid historical observations per metric are required. See README.md.
    """
    report = analyze_astronaut_data(history, measurements)
    analyzed_at = datetime.now(timezone.utc).isoformat()
    results = []
    for row in report["results"]:
        baselines = report["baselines"][row["astronaut_id"]]
        issues = list(row["data_quality_issues"])
        # The original API reports one issue per metric. Expose both missing
        # measurement and insufficient history when both apply in this adapter.
        for metric in METRICS:
            issue = f"{metric}: insufficient historical values"
            if metric not in baselines and issue not in issues:
                issues.append(issue)
        anomalies = [
            {"metric": anomaly["metric"],
             "current_value": anomaly["value"],
             "baseline_mean": baselines[anomaly["metric"]]["mean"],
             "kinds": list(anomaly["kinds"]),
             "explanation": anomaly["explanation"]}
            for anomaly in row["anomalies"]
        ]
        recommendations = []
        if anomalies:
            metrics = ", ".join(anomaly["metric"] for anomaly in anomalies)
            explanation = f"Statistical changes detected in: {metrics}."
            recommendations.append(
                f"Verify the recorded values and measurement consistency for: {metrics}."
            )
            recommendations.append("Review subsequent daily measurements for continued statistical changes.")
            if any("persistent_change" in anomaly["kinds"] for anomaly in anomalies):
                recommendations.append("Review the consecutive-day pattern alongside the supplied historical records.")
        elif issues:
            explanation = "The day could not be fully assessed; no anomalies were detected in the assessable metrics."
        else:
            explanation = "All metrics were assessed; no configured statistical alert rule was triggered."
        if issues:
            explanation += " Assessment is incomplete: " + "; ".join(issues) + "."
            if any("missing measurement" in issue for issue in issues):
                recommendations.append("Complete or verify missing measurements before reassessing this day.")
            if any("insufficient historical values" in issue for issue in issues):
                recommendations.append("Supply at least 7 valid earlier daily observations for each metric lacking a baseline.")
        results.append({
            "astronaut_id": row["astronaut_id"], "date": row["date"],
            "analyzed_at": analyzed_at, "alert_level": row["alert_level"],
            "detected_anomalies": anomalies, "explanation": explanation,
            "recommendations": recommendations, "data_quality_issues": issues,
        })
    return {"results": results}
