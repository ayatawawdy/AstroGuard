"""Combined alerts describe existing detections within one astronaut/day."""
import json
import unittest

from ai import analyze_astronaut_data, analyze_for_backend, analyze_health_data
from test_ai import records
from test_team_schema import long_rows, without_activity


class CombinedAnomalyTests(unittest.TestCase):
    def setUp(self):
        self.history = records("2026-01-01", 7)

    def analyze(self, rows, history=None):
        return analyze_health_data(self.history if history is None else history, rows)["results"]

    def test_two_metrics_and_directions(self):
        row = self.analyze(records("2026-01-08", 1, sleep_duration=6, heart_rate=80))[0]
        combined = row["combined_anomalies"]
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["metrics"], ["sleep_duration", "heart_rate"])
        self.assertEqual(combined[0]["contributors"], [
            {"metric": "sleep_duration", "direction": "below"},
            {"metric": "heart_rate", "direction": "above"}])
        self.assertIn("occurred together on the same day", combined[0]["explanation"])
        self.assertEqual(row["alert_level"], "warning")
        self.assertEqual(len(row["detected_anomalies"]), 2)
        self.assertEqual(len(row["recommendations"]), 2)
        json.dumps(row, allow_nan=False)

    def test_three_metrics(self):
        row = self.analyze(records("2026-01-08", 1, sleep_duration=6, heart_rate=80, stress_level=10))[0]
        self.assertEqual(row["combined_anomalies"][0]["metrics"],
                         ["sleep_duration", "heart_rate", "stress_level"])

    def test_one_metric(self):
        row = self.analyze(records("2026-01-08", 1, sleep_duration=6))[0]
        self.assertNotIn("combined_anomalies", row)
        self.assertEqual(len(row["detected_anomalies"]), 1)

    def test_no_anomalies(self):
        row = self.analyze(records("2026-01-08", 1))[0]
        self.assertNotIn("combined_anomalies", row)
        self.assertEqual(row["alert_level"], "normal")

    def test_missing_optional_activity_and_long_form(self):
        history = without_activity(self.history)
        current = without_activity(records("2026-01-08", 1, sleep_duration=6, stress_level=8))
        for past, rows in ((history, current), (long_rows(history), long_rows(current))):
            row = self.analyze(rows, past)[0]
            self.assertEqual(row["combined_anomalies"][0]["metrics"], ["sleep_duration", "stress_level"])
            self.assertEqual(row["data_quality_issues"], [])
            self.assertEqual(row["status"], "success")

    def test_different_dates_do_not_combine(self):
        rows = self.analyze(records("2026-01-08", 1, sleep_duration=6) +
                            records("2026-01-09", 1, heart_rate=80))
        self.assertTrue(all("combined_anomalies" not in row for row in rows))

    def test_different_astronauts_do_not_combine(self):
        history = self.history + records("2026-01-01", 7, "SIM-B")
        rows = self.analyze(records("2026-01-08", 1, sleep_duration=6) +
                            records("2026-01-08", 1, "SIM-B", heart_rate=80), history)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("combined_anomalies" not in row for row in rows))

    def test_persistent_and_deviation_count_once_per_metric(self):
        rows = self.analyze(records("2026-01-08", 3, sleep_duration=7.4, heart_rate=80))
        self.assertNotIn("combined_anomalies", rows[0])
        self.assertEqual(rows[-1]["combined_anomalies"][0]["metrics"], ["sleep_duration", "heart_rate"])
        self.assertEqual(rows[-1]["detected_anomalies"][1]["kinds"], ["deviation", "persistent_change"])

    def test_activity_contributes_and_missing_required_data_stays_incomplete(self):
        row = self.analyze(records("2026-01-08", 1, activity_minutes=90,
                                   heart_rate=80, stress_level=None))[0]
        self.assertEqual(row["combined_anomalies"][0]["metrics"], ["heart_rate", "activity_minutes"])
        self.assertEqual(row["status"], "insufficient_data")
        self.assertEqual(row["alert_level"], "warning")
        self.assertIn("stress_level: missing measurement", row["data_quality_issues"])

    def test_all_entry_points_preserve_existing_contract(self):
        current = records("2026-01-08", 1, sleep_duration=6, heart_rate=80)
        core = analyze_astronaut_data(self.history, current)["results"][0]
        backend = analyze_for_backend(self.history, current)["results"][0]
        health = self.analyze(current)[0]
        self.assertEqual(core["combined_anomalies"], backend["combined_anomalies"])
        self.assertEqual(backend["combined_anomalies"], health["combined_anomalies"])
        for row in (backend, health):
            self.assertEqual(set(row) - {"combined_anomalies", "status"},
                {"astronaut_id", "date", "analyzed_at", "alert_level", "detected_anomalies",
                 "explanation", "recommendations", "data_quality_issues"})
            self.assertEqual(set(row["detected_anomalies"][0]),
                {"metric", "current_value", "baseline_mean", "kinds", "explanation"})
            json.dumps(row, allow_nan=False)
