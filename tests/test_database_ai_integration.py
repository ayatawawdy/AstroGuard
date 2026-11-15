"""End-to-end tests using the actual SQLite schema and AI implementation."""
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.ai_service import analyze_astronaut_day
from backend.database import get_connection, initialize_database


class DatabaseAIIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "test.db"
        initialize_database(self.path)
        with closing(get_connection(self.path)) as conn, conn:
            conn.executemany("INSERT INTO astronauts VALUES (?, ?)", [("A", "A"), ("B", "B")])
            conn.executemany("INSERT INTO missions VALUES (?, ?)", [("M", "M"), ("N", "N")])
        self.counter = 0

    def insert(self, timestamp, metric, value, *, astronaut="A", mission="M", unit=None, identifier=None):
        self.counter += 1
        units = {"sleep_duration": "hours", "heart_rate": "bpm", "stress_level": "1-10", "activity_minutes": "minutes"}
        with closing(get_connection(self.path)) as conn, conn:
            conn.execute("INSERT INTO measurements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (identifier or f"R-{self.counter:05}", astronaut, mission, timestamp,
                          metric, value, unit or units.get(metric, "unused"), "synthetic", 1, None))

    def day(self, day, **changes):
        for metric, value in {"sleep_duration": 8, "heart_rate": 60, "stress_level": 2, **changes}.items():
            if value is not None:
                self.insert(day + "T08:00:00Z", metric, value)

    def history(self, count=7):
        for n in range(count):
            self.day((date(2026, 1, 1) + timedelta(days=n)).isoformat())

    def analyze(self, **options):
        report = analyze_astronaut_day("A", "M", "2026-01-08", db_path=self.path, **options)
        json.dumps(report, allow_nan=False)
        self.assertEqual(len(report["results"]), 1)
        return report["results"][0]

    def test_normal_without_optional_activity(self):
        self.history()
        self.day("2026-01-08")
        before = self.path.read_bytes()
        row = self.analyze()
        self.assertEqual((row["status"], row["alert_level"]), ("success", "normal"))
        self.assertEqual(row["data_quality_issues"], [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_anomalies_combined_and_baseline_no_future_leakage(self):
        self.history()
        self.day("2026-01-08", sleep_duration=6, heart_rate=80)
        for day in ("2026-01-09", "2099-01-01"):
            self.day(day, sleep_duration=0, heart_rate=1000)
        row = self.analyze()
        self.assertEqual(row["alert_level"], "warning")
        self.assertEqual(row["combined_anomalies"][0]["metrics"], ["sleep_duration", "heart_rate"])
        self.assertEqual([a["baseline_mean"] for a in row["detected_anomalies"]], [8, 60])

    def test_insufficient_history_target_not_counted(self):
        self.history(6)
        self.day("2026-01-08")
        row = self.analyze()
        self.assertEqual(row["status"], "insufficient_data")
        self.assertIsNone(row["alert_level"])
        self.assertIn("sleep_duration: insufficient historical values", row["data_quality_issues"])

    def test_missing_required_metric(self):
        self.history()
        self.day("2026-01-08", stress_level=None)
        row = self.analyze()
        self.assertEqual(row["status"], "insufficient_data")
        self.assertIn("stress_level: missing measurement", row["data_quality_issues"])

    def test_empty_history_and_missing_target(self):
        row = self.analyze()
        self.assertEqual(row["status"], "insufficient_data")
        self.assertIn("heart_rate: missing measurement", row["data_quality_issues"])
        self.history()
        self.assertIn("sleep_duration: missing measurement", self.analyze()["data_quality_issues"])

    def test_duplicate_target_rejected_or_latest_selected(self):
        self.history()
        self.day("2026-01-08")
        self.insert("2026-01-08T09:00:00+00:00", "heart_rate", 80)
        with self.assertRaisesRegex(ValueError, "Duplicate daily metric"):
            self.analyze()
        row = self.analyze(aggregation="latest")
        self.assertEqual(row["detected_anomalies"][0]["current_value"], 80)

    def test_duplicate_history_not_extra_baseline_observation(self):
        self.history(6)
        self.insert("2026-01-01T09:00:00Z", "heart_rate", 60)
        self.day("2026-01-08")
        with self.assertRaisesRegex(ValueError, "Duplicate daily metric"):
            self.analyze()
        row = self.analyze(aggregation="latest")
        self.assertIn("heart_rate: insufficient historical values", row["data_quality_issues"])

    def test_latest_tie_break_and_utc_day(self):
        self.history()
        self.day("2026-01-08")
        self.insert("2026-01-09T01:00:00+02:00", "heart_rate", 80, identifier="Z")
        self.insert("2026-01-08T23:00:00Z", "heart_rate", 60, identifier="Y")
        self.assertEqual(self.analyze(aggregation="latest")["detected_anomalies"][0]["current_value"], 80)

    def test_astronaut_mission_and_metric_isolation(self):
        self.history()
        self.day("2026-01-08")
        self.insert("2026-01-08T10:00:00Z", "heart_rate", 100, astronaut="B")
        self.insert("2026-01-08T10:00:00Z", "heart_rate", 100, mission="N")
        self.insert("2026-01-08T10:00:00Z", "mood", 5)
        self.assertEqual(self.analyze()["alert_level"], "normal")

    def test_activity_when_stored(self):
        self.history()
        for n in range(1, 8):
            self.insert(f"2026-01-0{n}T08:00:00Z", "activity_minutes", 45)
        self.day("2026-01-08")
        self.insert("2026-01-08T08:00:00Z", "activity_minutes", 90)
        self.assertEqual(self.analyze()["detected_anomalies"][0]["metric"], "activity_minutes")

    def test_invalid_arguments(self):
        for args in (("", "M", "2026-01-08"), ("unknown", "M", "2026-01-08"),
                     ("A", "unknown", "2026-01-08"), ("A", "M", "bad"),
                     ("A", "M", "2099-01-01")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                analyze_astronaut_day(*args, db_path=self.path)
        with self.assertRaises(ValueError):
            self.analyze(aggregation="mean")

    def test_invalid_timestamp_unit_and_stress(self):
        for timestamp, metric, value, unit in (("bad", "heart_rate", 60, "bpm"),
                ("2026-01-08T08:00:00", "heart_rate", 60, "bpm"),
                ("2026-01-08T08:00:00Z", "heart_rate", 60, "hours"),
                ("2026-01-08T08:00:00Z", "stress_level", 11, "1-10")):
            with self.subTest(timestamp=timestamp, metric=metric, value=value):
                with closing(get_connection(self.path)) as conn, conn:
                    conn.execute("DELETE FROM measurements")
                self.insert(timestamp, metric, value, unit=unit)
                with self.assertRaises(ValueError):
                    self.analyze()
