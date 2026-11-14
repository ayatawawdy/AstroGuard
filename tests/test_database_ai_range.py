"""SQLite range integration: preserve AI persistence and frozen baselines."""
import json
import unittest

from backend.ai_service import analyze_astronaut_day, analyze_astronaut_range
import test_database_ai_integration as fixtures


class DatabaseAIRangeTests(unittest.TestCase):
    setUp = fixtures.DatabaseAIIntegrationTests.setUp
    insert = fixtures.DatabaseAIIntegrationTests.insert
    day = fixtures.DatabaseAIIntegrationTests.day
    history = fixtures.DatabaseAIIntegrationTests.history

    def analyze(self, start="2026-01-08", end="2026-01-10", astronaut="A", **options):
        report = analyze_astronaut_range(astronaut, "M", start, end, db_path=self.path, **options)
        json.dumps(report, allow_nan=False)
        return report["results"]

    def test_consecutive_persistence_and_combined(self):
        self.history()
        for day in ("08", "09", "10"):
            self.day("2026-01-" + day, sleep_duration=7.4, heart_rate=65)
        rows = self.analyze()
        self.assertEqual([r["alert_level"] for r in rows], ["normal", "normal", "warning"])
        self.assertEqual([a["kinds"] for a in rows[-1]["detected_anomalies"]],
                         [["persistent_change"], ["persistent_change"]])
        self.assertEqual(rows[-1]["combined_anomalies"][0]["metrics"], ["sleep_duration", "heart_rate"])
        self.assertIn("3 consecutive days", rows[-1]["detected_anomalies"][0]["explanation"])
        self.assertEqual(len({r["analyzed_at"] for r in rows}), 1)

    def test_nonconsecutive_anomalies(self):
        self.history()
        for day in ("08", "10", "12"):
            self.day("2026-01-" + day, sleep_duration=7.4)
        rows = self.analyze(end="2026-01-12")
        self.assertTrue(all(not row["detected_anomalies"] for row in rows))
        self.assertEqual(rows[1]["status"], "insufficient_data")

    def test_missing_metric_breaks_only_its_run(self):
        self.history()
        self.day("2026-01-08", sleep_duration=7.4, heart_rate=65)
        self.day("2026-01-09", sleep_duration=None, heart_rate=65)
        self.day("2026-01-10", sleep_duration=7.4, heart_rate=65)
        rows = self.analyze()
        self.assertIn("sleep_duration: missing measurement", rows[1]["data_quality_issues"])
        self.assertEqual([a["metric"] for a in rows[-1]["detected_anomalies"]], ["heart_rate"])

    def test_single_day_anomaly_and_day_service_equivalence(self):
        self.history()
        self.day("2026-01-08", sleep_duration=6)
        ranged = self.analyze(end="2026-01-08")[0]
        single = analyze_astronaut_day("A", "M", "2026-01-08", db_path=self.path)["results"][0]
        for row in (ranged, single):
            row.pop("analyzed_at")
        self.assertEqual(ranged, single)
        self.assertEqual(ranged["detected_anomalies"][0]["kinds"], ["deviation"])

    def test_other_astronaut_and_mission_do_not_contribute(self):
        self.history()
        for day in ("08", "09", "10"):
            self.day("2026-01-" + day)
            self.insert(f"2026-01-{day}T08:00:00Z", "heart_rate", 100, astronaut="B")
            self.insert(f"2026-01-{day}T08:00:00Z", "heart_rate", 100, mission="N")
        self.assertTrue(all(r["alert_level"] == "normal" for r in self.analyze()))
        self.assertTrue(all(r["astronaut_id"] == "B" and r["status"] == "insufficient_data"
                            for r in self.analyze(astronaut="B")))

    def test_boundaries_and_frozen_history_no_future_leakage(self):
        self.history()
        for day in ("08", "09", "10", "11"):
            self.day("2026-01-" + day, heart_rate=80)
        self.day("2099-01-01", heart_rate=1000)
        rows = self.analyze()
        self.assertEqual([r["date"] for r in rows], ["2026-01-08", "2026-01-09", "2026-01-10"])
        self.assertTrue(all(r["detected_anomalies"][0]["baseline_mean"] == 60 for r in rows))
        self.assertEqual(rows[-1]["detected_anomalies"][0]["kinds"], ["deviation", "persistent_change"])

    def test_no_pre_range_persistence(self):
        self.history()
        for day in ("08", "09", "10"):
            self.day("2026-01-" + day, heart_rate=80)
        rows = self.analyze(start="2026-01-10")
        self.assertFalse(any("persistent_change" in a["kinds"] for a in rows[0]["detected_anomalies"]))

    def test_insufficient_history_does_not_grow_during_range(self):
        self.history(6)
        for day in ("08", "09", "10"):
            self.day("2026-01-" + day)
        rows = self.analyze()
        self.assertTrue(all(r["status"] == "insufficient_data" for r in rows))
        self.assertIn("heart_rate: insufficient historical values", rows[-1]["data_quality_issues"])

    def test_empty_database_and_missing_optional_activity(self):
        rows = self.analyze()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["status"] == "insufficient_data" for r in rows))
        self.assertTrue(all(not any("activity_minutes" in issue for issue in r["data_quality_issues"])
                            for r in rows))

    def test_duplicates_latest_and_utc_boundaries(self):
        self.history()
        for day in ("08", "09", "10"):
            self.day("2026-01-" + day)
            self.insert(f"2026-01-{day}T23:00:00+00:00", "heart_rate", 65)
        self.insert("2026-01-11T01:00:00+02:00", "heart_rate", 65, identifier="Z")
        with self.assertRaisesRegex(ValueError, "Duplicate daily metric"):
            self.analyze()
        rows = self.analyze(aggregation="latest")
        self.assertEqual(rows[-1]["detected_anomalies"][0]["kinds"], ["persistent_change"])

    def test_duplicate_history_and_outside_range(self):
        self.history()
        self.insert("2026-01-01T09:00:00Z", "heart_rate", 60)
        self.day("2026-01-08")
        self.day("2026-01-11")
        self.insert("2026-01-11T09:00:00Z", "heart_rate", 60)
        with self.assertRaisesRegex(ValueError, "Duplicate daily metric"):
            self.analyze()
        self.assertEqual(self.analyze(aggregation="latest")[0]["alert_level"], "normal")

    def test_invalid_ranges(self):
        for start, end in (("bad", "2026-01-10"), ("2026-01-08", None),
                           ("2026-01-10", "2026-01-08"), ("2026-01-08", "2099-01-01"),
                           ("20260108", "2026-01-10")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.analyze(start=start, end=end)
        with self.assertRaises(ValueError):
            self.analyze(aggregation="mean")
