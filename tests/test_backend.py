from datetime import datetime, timedelta, timezone
import json
import unittest

import pandas as pd

from ai import analyze_astronaut_data, analyze_for_backend
from ai.analysis import REQUIRED
from test_ai import records


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.history = records("2026-01-01", 7)

    def test_contract_and_real_utc_timestamp(self):
        before = datetime.now(timezone.utc)
        report = analyze_for_backend(self.history, records("2026-01-08", 1, sleep_duration=6))
        after = datetime.now(timezone.utc)
        row = report["results"][0]
        self.assertEqual(set(row), {"astronaut_id", "date", "analyzed_at", "alert_level",
                                   "detected_anomalies", "explanation", "recommendations",
                                   "data_quality_issues"})
        timestamp = datetime.fromisoformat(row["analyzed_at"])
        self.assertEqual(timestamp.utcoffset(), timedelta(0))
        self.assertLessEqual(before, timestamp)
        self.assertLessEqual(timestamp, after)
        self.assertEqual(row["date"], "2026-01-08")
        anomaly = row["detected_anomalies"][0]
        self.assertEqual(set(anomaly), {"metric", "current_value", "baseline_mean", "kinds", "explanation"})
        self.assertEqual(anomaly["current_value"], 6)
        self.assertEqual(anomaly["baseline_mean"], 8)
        self.assertEqual(anomaly["kinds"], ["deviation"])
        self.assertTrue(row["explanation"])
        json.dumps(report, allow_nan=False)

    def test_normal_has_no_change_recommendations(self):
        row = analyze_for_backend(self.history, records("2026-01-08", 1))["results"][0]
        self.assertEqual(row["alert_level"], "normal")
        self.assertEqual(row["detected_anomalies"], [])
        self.assertEqual(row["recommendations"], [])
        self.assertIn("All metrics were assessed", row["explanation"])

    def test_missing_value_is_not_normal(self):
        row = analyze_for_backend(self.history, records("2026-01-08", 1, sleep_duration=None))["results"][0]
        self.assertEqual(row["alert_level"], "insufficient_data")
        self.assertIn("sleep_duration: missing measurement", row["data_quality_issues"])
        self.assertIn("incomplete", row["explanation"])
        self.assertEqual(row["recommendations"], ["Complete or verify missing measurements before reassessing this day."])
        json.dumps(row, allow_nan=False)

    def test_seven_valid_values_required_per_metric(self):
        self.history[0]["sleep_duration"] = None
        row = analyze_for_backend(self.history, records("2026-01-08", 1))["results"][0]
        self.assertEqual(row["alert_level"], "insufficient_data")
        self.assertIn("sleep_duration: insufficient historical values", row["data_quality_issues"])
        self.assertIn("7 valid earlier", row["recommendations"][0])

    def test_both_missing_history_and_measurement_reported(self):
        row = analyze_for_backend(self.history[:6], records("2026-01-08", 1, sleep_duration=None))["results"][0]
        self.assertIn("sleep_duration: missing measurement", row["data_quality_issues"])
        self.assertIn("sleep_duration: insufficient historical values", row["data_quality_issues"])

    def test_warning_with_missing_data_and_conservative_recommendations(self):
        row = analyze_for_backend(self.history, records("2026-01-08", 1, sleep_duration=6,
                                                       stress_level=None))["results"][0]
        self.assertEqual(row["alert_level"], "warning")
        self.assertIn("incomplete", row["explanation"])
        self.assertEqual(row["recommendations"], [
            "Verify the recorded values and measurement consistency for: sleep_duration.",
            "Review subsequent daily measurements for continued statistical changes.",
            "Complete or verify missing measurements before reassessing this day."])

    def test_multiple_astronauts_dates_and_persistence_match_original(self):
        history = self.history + records("2026-01-01", 7, "SIM-B", sleep_duration=9)
        measurements = (records("2026-01-08", 3, "SIM-B", sleep_duration=9) +
                        records("2026-01-08", 3, sleep_duration=7.4))
        report = analyze_for_backend(history, list(reversed(measurements)))
        original = analyze_astronaut_data(history, measurements)
        self.assertEqual(len(report["results"]), 6)
        self.assertEqual(len({r["analyzed_at"] for r in report["results"]}), 1)
        for actual, previous in zip(report["results"], original["results"]):
            self.assertEqual((actual["astronaut_id"], actual["date"], actual["alert_level"]),
                             (previous["astronaut_id"], previous["date"], previous["alert_level"]))
            self.assertEqual([a["kinds"] for a in actual["detected_anomalies"]],
                             [a["kinds"] for a in previous["anomalies"]])
        persistent = report["results"][2]
        self.assertEqual(persistent["detected_anomalies"][0]["kinds"], ["persistent_change"])
        self.assertIn("consecutive-day pattern", persistent["recommendations"][-1])

    def test_empty_history_and_empty_evaluation(self):
        empty = pd.DataFrame(columns=REQUIRED)
        result = analyze_for_backend(empty, records("2026-01-08", 1))
        self.assertEqual(result["results"][0]["alert_level"], "insufficient_data")
        self.assertEqual(analyze_for_backend(self.history, empty), {"results": []})

    def test_validation_is_preserved(self):
        with self.assertRaises(ValueError):
            analyze_for_backend(self.history, records("2026-01-07", 1))


if __name__ == "__main__":
    unittest.main()
