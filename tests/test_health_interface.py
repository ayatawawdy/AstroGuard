from datetime import datetime, timedelta, timezone
import json
import unittest

import pandas as pd

from ai import analyze_astronaut_data, analyze_health_data
from ai.analysis import REQUIRED
from test_ai import records


class HealthInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.history = records("2026-01-01", 7)

    def test_all_status_alert_combinations(self):
        for missing, anomaly, status, alert in (
            (False, False, "success", "normal"),
            (False, True, "success", "warning"),
            (True, False, "insufficient_data", None),
            (True, True, "insufficient_data", "warning"),
        ):
            with self.subTest(missing=missing, anomaly=anomaly):
                row = analyze_health_data(self.history, records(
                    "2026-01-08", 1, stress_level=None if missing else 2,
                    sleep_hours=6 if anomaly else 8))["results"][0]
                self.assertEqual(row["status"], status)
                self.assertEqual(row["alert_level"], alert)
                self.assertEqual(bool(row["data_quality_issues"]), missing)
                self.assertEqual(bool(row["detected_anomalies"]), anomaly)
                if missing:
                    self.assertIn("stress_level: missing measurement", row["data_quality_issues"])
                    self.assertIn("incomplete", row["explanation"])
                json.dumps(row, allow_nan=False)

    def test_timestamp_and_exact_fields(self):
        before = datetime.now(timezone.utc)
        rows = analyze_health_data(self.history, records("2026-01-08", 2))["results"]
        after = datetime.now(timezone.utc)
        self.assertEqual(len({r["analyzed_at"] for r in rows}), 1)
        for row in rows:
            self.assertEqual(set(row), {"astronaut_id", "date", "analyzed_at", "status",
                "alert_level", "detected_anomalies", "explanation", "recommendations", "data_quality_issues"})
            self.assertRegex(row["analyzed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")
            timestamp = datetime.fromisoformat(row["analyzed_at"])
            self.assertEqual(timestamp.utcoffset(), timedelta(0))
            self.assertLessEqual(before, timestamp)
            self.assertLessEqual(timestamp, after)

    def test_stress_scale_in_history_and_measurements(self):
        for value in (0, 6, -1, 2.5, float("inf"), True, False, "2", "bad"):
            for target in ("history", "measurements"):
                with self.subTest(value=value, target=target):
                    history = records("2026-01-01", 7)
                    measurements = records("2026-01-08", 1)
                    (history if target == "history" else measurements)[0]["stress_level"] = value
                    with self.assertRaisesRegex(ValueError, "stress_level"):
                        analyze_health_data(history, measurements)

    def test_valid_stress_and_csv_integral_floats(self):
        for value in (1, 2, 3, 4, 5, 2.0, None, float("nan")):
            with self.subTest(value=value):
                result = analyze_health_data(pd.DataFrame(self.history),
                    pd.DataFrame(records("2026-01-08", 1, stress_level=value)))
                json.dumps(result, allow_nan=False)

    def test_missing_history_stress_does_not_count_toward_seven(self):
        self.history[0]["stress_level"] = None
        for sleep, expected_alert in ((8, None), (6, "warning")):
            row = analyze_health_data(self.history, records("2026-01-08", 1, sleep_hours=sleep))["results"][0]
            self.assertEqual(row["status"], "insufficient_data")
            self.assertEqual(row["alert_level"], expected_alert)
            self.assertIn("stress_level: insufficient historical values", row["data_quality_issues"])
            self.assertTrue(any("7 valid earlier" in r for r in row["recommendations"]))

    def test_baseline_separation_and_legacy_compatibility(self):
        with self.assertRaisesRegex(ValueError, "history must precede"):
            analyze_health_data(self.history, records("2026-01-07", 1))
        legacy = analyze_astronaut_data(self.history, records("2026-01-08", 1, stress_level=0))
        self.assertNotIn("status", legacy["results"][0])

    def test_multiple_astronauts_dates_and_preserved_persistence(self):
        history = self.history + records("2026-01-01", 7, "SIM-B", sleep_hours=9)
        measurements = records("2026-01-08", 3, "SIM-B", sleep_hours=9)
        measurements += records("2026-01-08", 3, sleep_hours=7.4)
        rows = analyze_health_data(history, list(reversed(measurements)))["results"]
        original = analyze_astronaut_data(history, measurements)
        self.assertEqual(len(rows), 6)
        for actual, previous in zip(rows, original["results"]):
            self.assertEqual((actual["astronaut_id"], actual["date"], actual["alert_level"]),
                             (previous["astronaut_id"], previous["date"], previous["alert_level"]))
        anomaly = rows[2]["detected_anomalies"][0]
        self.assertEqual(anomaly["baseline_mean"], 8)
        self.assertEqual(anomaly["kinds"], ["persistent_change"])
        self.assertIn("consecutive-day pattern", rows[2]["recommendations"][-1])

    def test_no_fatigue_required_and_empty_evaluation(self):
        self.assertNotIn("fatigue_level", self.history[0])
        row = analyze_health_data(self.history, records("2026-01-08", 1))["results"][0]
        self.assertEqual(row["status"], "success")
        self.assertEqual(analyze_health_data(self.history, pd.DataFrame(columns=REQUIRED)), {"results": []})


if __name__ == "__main__":
    unittest.main()
