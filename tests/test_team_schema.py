"""AI integration checks against the team's actual storage format."""
import json
from pathlib import Path
import unittest

import pandas as pd

from ai import analyze_astronaut_data, analyze_for_backend, analyze_health_data
from test_ai import records


def without_activity(rows):
    return [{k: v for k, v in row.items() if k != "activity_minutes"} for row in rows]


def long_rows(rows):
    units = {"sleep_duration": "hours", "heart_rate": "bpm", "stress_level": "1-10"}
    return [{"astronaut_id": row["astronaut_id"], "mission_id": "SIM-M",
             "timestamp_utc": row["date"] + "T08:00:00Z", "metric_type": metric,
             "value": row[metric], "unit": unit}
            for row in rows for metric, unit in units.items()]


class TeamSchemaTests(unittest.TestCase):
    def test_optional_activity_and_long_form_match_daily(self):
        history = without_activity(records("2026-01-01", 7))
        for overrides in ({}, {"heart_rate": 80}, {"sleep_duration": 7.4}):
            daily = without_activity(records("2026-01-08", 3, **overrides))
            for entry in (analyze_astronaut_data, analyze_for_backend, analyze_health_data):
                with self.subTest(entry=entry.__name__, overrides=overrides):
                    expected = entry(history, daily)
                    actual = entry(long_rows(history), long_rows(daily))
                    for report in (expected, actual):
                        for row in report["results"]:
                            row.pop("analyzed_at", None)
                            self.assertEqual(row["data_quality_issues"], [])
                    self.assertEqual(actual, expected)
                    json.dumps(actual, allow_nan=False)

    def test_activity_still_detected_and_null_is_optional(self):
        history = records("2026-01-01", 7)
        row = analyze_health_data(history, records("2026-01-08", 1, activity_minutes=90))["results"][0]
        self.assertEqual(row["detected_anomalies"][0]["metric"], "activity_minutes")
        row = analyze_health_data(history, records("2026-01-08", 1, activity_minutes=None))["results"][0]
        self.assertEqual(row["status"], "success")

    def test_actual_team_csv(self):
        data = pd.read_csv(Path(__file__).resolve().parents[1] / "data/synthetic/health_measurements.csv")
        history = data[data.timestamp_utc < "2026-10-08"]
        current = data[(data.timestamp_utc >= "2026-10-08") & (data.timestamp_utc < "2026-10-09")]
        rows = analyze_health_data(history, current)["results"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["status"] == "success" for row in rows))
        json.dumps(rows, allow_nan=False)

    def test_long_form_validation_and_missing_required_reading(self):
        history = long_rows(records("2026-01-01", 7))
        current = long_rows(records("2026-01-08", 1))
        for change in ({"unit": "minutes"}, {"timestamp_utc": "bad"},
                       {"timestamp_utc": "2026-01-08T08:00:00"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                analyze_health_data(history, [{**current[0], **change}, *current[1:]])
        with self.assertRaisesRegex(ValueError, "duplicate daily metric"):
            analyze_health_data(history, current + current[:1])
        row = analyze_health_data(history, current[1:])["results"][0]
        self.assertIn("sleep_duration: missing measurement", row["data_quality_issues"])

    def test_stress_scale_all_entry_points(self):
        for entry in (analyze_astronaut_data, analyze_for_backend, analyze_health_data):
            for value in range(1, 11):
                entry(records("2026-01-01", 7, stress_level=value),
                      records("2026-01-08", 1, stress_level=value))
            for value in (0, 11, -1, 1.5, True, "3", float("inf")):
                with self.subTest(entry=entry.__name__, value=value), self.assertRaises(ValueError):
                    entry(records("2026-01-01", 7), records("2026-01-08", 1, stress_level=value))
