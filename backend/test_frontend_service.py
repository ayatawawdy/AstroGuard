"""Public Streamlit interface checks against isolated SQLite databases."""
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend import frontend_service as frontend
from backend import services, ai_service
from backend.database import get_connection, initialize_database


class FrontendServiceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "test.db"
        initialize_database(self.path)
        with closing(get_connection(self.path)) as conn, conn:
            conn.executemany("INSERT INTO astronauts VALUES (?, ?)", [("A", None), ("B", "Bee")])
            conn.executemany("INSERT INTO missions VALUES (?, ?)", [("M", "Mission"), ("N", None)])
        self.counter = 0

    def insert(self, day, metric="heart_rate", value=60, mission="M", time="08:00:00Z"):
        self.counter += 1
        unit = {"heart_rate": "bpm", "sleep_duration": "hours", "stress_level": "1-10"}[metric]
        with closing(get_connection(self.path)) as conn, conn:
            conn.execute("INSERT INTO measurements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (f"R-{self.counter:05}", "A", mission, f"{day}T{time}",
                          metric, value, unit, "synthetic", 1, None))

    def seed(self):
        for n in range(10):
            day = (date(2026, 1, 1) + timedelta(days=n)).isoformat()
            for metric, value in (("heart_rate", 60 if n < 7 else 65),
                                  ("sleep_duration", 8 if n < 7 else 7.4), ("stress_level", 2)):
                self.insert(day, metric, value)

    def test_astronaut_and_mission_retrieval(self):
        self.assertEqual(frontend.get_astronauts(db_path=self.path),
                         [{"astronaut_id": "A", "display_name": None}, {"astronaut_id": "B", "display_name": "Bee"}])
        self.assertEqual(frontend.get_missions("A", db_path=self.path), [])
        self.insert("2026-01-01", mission="N")
        self.insert("2026-01-02")
        self.insert("2026-01-03")
        self.assertEqual(frontend.get_missions("A", db_path=self.path),
                         [{"mission_id": "M", "mission_name": "Mission"}, {"mission_id": "N", "mission_name": None}])
        self.assertEqual(frontend.get_missions("B", db_path=self.path), [])
        self.assertEqual(frontend.get_missions("unknown", db_path=self.path), [])

    def test_measurement_schema_filters_order_and_limit(self):
        self.insert("2026-01-01")
        self.insert("2026-01-02", "sleep_duration", 8)
        self.insert("2026-01-03", mission="N")
        rows = frontend.get_measurements("A", mission_id="M", db_path=self.path)
        self.assertEqual([r["metric_type"] for r in rows], ["sleep_duration", "heart_rate"])
        self.assertEqual(set(rows[0]), {"measurement_id", "astronaut_id", "mission_id", "timestamp_utc",
                         "metric_type", "value", "unit", "source", "is_synthetic", "context"})
        self.assertEqual(frontend.get_measurements("A", "heart_rate", 1, db_path=self.path)[0]["mission_id"], "N")
        self.assertEqual(frontend.get_measurements("A", mission_id="unknown", db_path=self.path), [])
        self.assertEqual(frontend.get_measurements("unknown", db_path=self.path), [])
        json.dumps(rows, allow_nan=False)

    def test_single_day_report(self):
        self.seed()
        report = frontend.analyze_astronaut_day("A", "M", "2026-01-08", db_path=self.path)
        row = report["results"][0]
        self.assertEqual(row["alert_level"], "normal")
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["data_quality_issues"], [])
        self.assertEqual(set(row), {"astronaut_id", "date", "analyzed_at", "status", "alert_level",
                         "detected_anomalies", "explanation", "recommendations", "data_quality_issues"})
        json.dumps(report, allow_nan=False)

    def test_range_persistence_combined_and_actual_response_fields(self):
        self.seed()
        report = frontend.analyze_astronaut_range("A", "M", "2026-01-08", "2026-01-10", db_path=self.path)
        row = report["results"][-1]
        self.assertEqual(row["alert_level"], "warning")
        self.assertEqual(row["combined_anomalies"][0]["metrics"], ["sleep_duration", "heart_rate"])
        self.assertEqual(row["detected_anomalies"][0]["kinds"], ["persistent_change"])
        self.assertEqual(set(row["detected_anomalies"][0]), {"metric", "current_value", "baseline_mean", "kinds", "explanation"})
        self.assertTrue(row["recommendations"])
        json.dumps(report, allow_nan=False)

    def test_duplicates_are_raw_for_charts_and_explicit_for_ai(self):
        self.seed()
        self.insert("2026-01-08", value=80, time="09:00:00Z")
        rows = frontend.get_measurements("A", "heart_rate", mission_id="M", db_path=self.path)
        self.assertEqual(sum(r["timestamp_utc"].startswith("2026-01-08") for r in rows), 2)
        with self.assertRaisesRegex(ValueError, "Duplicate daily metric"):
            frontend.analyze_astronaut_day("A", "M", "2026-01-08", db_path=self.path)
        row = frontend.analyze_astronaut_day("A", "M", "2026-01-08", aggregation="latest", db_path=self.path)["results"][0]
        self.assertEqual(row["detected_anomalies"][0]["current_value"], 80)

    def test_empty_missing_and_invalid_analysis(self):
        row = frontend.analyze_astronaut_day("A", "M", "2026-01-08", db_path=self.path)["results"][0]
        self.assertEqual(row["status"], "insufficient_data")
        self.assertIsNone(row["alert_level"])
        for args in (("unknown", "M", "2026-01-08"), ("A", "unknown", "2026-01-08"), ("A", "M", "bad")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                frontend.analyze_astronaut_day(*args, db_path=self.path)
        with self.assertRaises(ValueError):
            frontend.analyze_astronaut_range("A", "M", "2026-01-10", "2026-01-08", db_path=self.path)

    def test_invalid_retrieval_arguments(self):
        for value in (None, "", "  ", 42):
            for func in (frontend.get_missions, frontend.get_measurements):
                with self.subTest(value=value, func=func), self.assertRaises(ValueError):
                    func(value, db_path=self.path)
        for limit in (True, 0, 1001, 1.5, "1"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                frontend.get_measurements("A", limit=limit, db_path=self.path)
        for options in ({"mission_id": ""}, {"metric_type": 2}):
            with self.assertRaises(ValueError):
                frontend.get_measurements("A", db_path=self.path, **options)

    def test_empty_astronaut_table(self):
        with closing(get_connection(self.path)) as conn, conn:
            conn.execute("DELETE FROM astronauts")
        self.assertEqual(frontend.get_astronauts(db_path=self.path), [])

    def test_interface_reuses_existing_implementations(self):
        for name in ("get_astronauts", "get_missions", "get_measurements"):
            self.assertIs(getattr(frontend, name), getattr(services, name))
        for name in ("analyze_astronaut_day", "analyze_astronaut_range"):
            self.assertIs(getattr(frontend, name), getattr(ai_service, name))
