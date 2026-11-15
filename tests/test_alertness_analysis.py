"""Personalized alertness analysis: four statuses, baselines, task context, isolation."""

import tempfile
import unittest
from contextlib import closing, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from backend import sleep_alertness as sa
from backend.alertness_analysis import (DEFAULT_CONFIG, STATUS_LABELS, TASK_CONTEXTS,
                                        analyze_alertness, build_nights)
from backend.database import get_connection, initialize_database
from backend.import_sleep_alertness import import_sleep_alertness
from data.generate_sleep_alertness import write_dataset

MISSION = "MISSION-SLEEP-DEMO-01"
TRIALS = [{"reaction_time_ms": rt} for rt in (250, 255, 260, 265, 270, 248, 252, 258)]


def demo_database(temp: Path) -> Path:
    """Fresh temporary database with the generated demo scenarios."""
    db = temp / "demo.db"
    write_dataset(temp / "dataset")
    with redirect_stdout(StringIO()):
        import_sleep_alertness(temp / "dataset", db_path=db)
    return db


class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.db = demo_database(Path(cls._temp.name))

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def analyze(self, astronaut, **kwargs):
        return analyze_alertness(astronaut, MISSION, db_path=self.db, **kwargs)

    def test_result_structure(self):
        result = self.analyze("AST-S01")
        for key in ("astronaut_id", "mission_id", "assessment_timestamp_utc", "status",
                    "status_label", "supporting_observations", "baseline_comparisons",
                    "data_quality_issues", "explanation", "suggested_action", "task_context",
                    "is_synthetic_input", "evidence", "rule_checks", "limitations"):
            self.assertIn(key, result)
        self.assertIn(result["status"], STATUS_LABELS)
        self.assertTrue(result["is_synthetic_input"])
        self.assertEqual(result["astronaut_id"], "AST-S01")

    def test_scenario_1_normal(self):
        result = self.analyze("AST-S01")
        self.assertEqual(result["status"], "normal")
        self.assertFalse(any(c["triggered"] for c in result["rule_checks"]))

    def test_scenario_2_shift_without_performance_decline(self):
        result = self.analyze("AST-S02")
        self.assertEqual(result["status"], "attention_needed")
        triggered = [c["rule"] for c in result["rule_checks"] if c["triggered"]]
        self.assertEqual(len(triggered), 1, triggered)   # only the schedule rule fired
        self.assertTrue(triggered[0].startswith("|mid-sleep shift|"))
        self.assertIn("does not indicate reduced concentration", result["explanation"])
        self.assertNotIn("reduction in alertness", result["explanation"])

    def test_scenario_3_rest_recommended(self):
        result = self.analyze("AST-S03")
        self.assertEqual(result["status"], "rest_recommended")
        sleep = result["baseline_comparisons"]["sleep"]
        rt = result["baseline_comparisons"]["reaction_time"]
        self.assertLess(sleep["ratio_to_baseline"], DEFAULT_CONFIG.sleep_reduction_ratio)
        self.assertGreater(rt["recent_mean_median_rt_ms"], rt["baseline_mean_median_rt_ms"])
        self.assertTrue(result["evidence"]["recent_assessment_ids"])
        self.assertIn("rest break", result["suggested_action"])

    def test_scenario_4_insufficient_data(self):
        result = self.analyze("AST-S04")
        self.assertEqual(result["status"], "insufficient_data")
        self.assertTrue(result["data_quality_issues"])
        self.assertIn("No fatigue conclusion", result["explanation"])
        self.assertEqual(result["rule_checks"], [])

    def test_baseline_uses_only_earlier_observations(self):
        for astronaut in ("AST-S01", "AST-S02", "AST-S03"):
            with self.subTest(astronaut=astronaut):
                result = self.analyze(astronaut)
                ev = result["evidence"]
                self.assertFalse(set(ev["baseline_sleep_session_ids"])
                                 & set(ev["recent_sleep_session_ids"]))
                self.assertFalse(set(ev["baseline_assessment_ids"])
                                 & set(ev["recent_assessment_ids"]))
                tests = {a["assessment_id"]: a["assessment_utc"] for a in
                         sa.get_alertness_assessments(astronaut, db_path=self.db)}
                self.assertLess(max(tests[i] for i in ev["baseline_assessment_ids"]),
                                min(tests[i] for i in ev["recent_assessment_ids"]))

    def test_task_context_changes_wording_not_status(self):
        statuses = set()
        for key in TASK_CONTEXTS:
            result = self.analyze("AST-S03", task_context=key)
            statuses.add(result["status"])
            self.assertIn(TASK_CONTEXTS[key], result["suggested_action"])
            if key == "safety_critical_operation":
                self.assertIn("does not pause or terminate", result["suggested_action"])
                self.assertIn("Mission Control", result["suggested_action"])
        self.assertEqual(statuses, {"rest_recommended"})
        with self.assertRaises(ValueError):
            self.analyze("AST-S03", task_context="spacewalk")

    def test_thresholds_are_configurable(self):
        lenient = replace(DEFAULT_CONFIG, sleep_reduction_ratio=0.5)
        result = self.analyze("AST-S03", config=lenient)
        self.assertEqual(result["status"], "attention_needed")   # performance decline only
        self.assertEqual(result["config"]["sleep_reduction_ratio"], 0.5)

    def test_scenarios_are_isolated(self):
        before = self.analyze("AST-S01")
        self.analyze("AST-S03")
        self.assertEqual(self.analyze("AST-S01")["status"], before["status"])
        self.assertEqual(analyze_alertness("AST-S01", "MISSION-DEMO-01",
                                           db_path=self.db)["status"], "insufficient_data")


class MutationTests(unittest.TestCase):
    """Tests that add records use their own fresh database."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = demo_database(Path(temp.name))

    def test_new_assessment_is_excluded_from_its_own_baseline(self):
        before = analyze_alertness("AST-S01", MISSION, db_path=self.db)
        slow = [{"reaction_time_ms": rt} for rt in (480, 520, 610, None, 455, 470, 530, 495)]
        new_id = sa.add_alertness_assessment(
            "AST-S01", MISSION, "2026-10-15T09:30:00Z", slow, protocol_version="test",
            timing_method="software_monotonic_clock", db_path=self.db)
        after = analyze_alertness("AST-S01", MISSION, db_path=self.db)
        self.assertIn(new_id, after["evidence"]["recent_assessment_ids"])
        self.assertNotIn(new_id, after["evidence"]["baseline_assessment_ids"])
        self.assertEqual(after["baseline_comparisons"]["reaction_time"]["baseline_mean_median_rt_ms"],
                         before["baseline_comparisons"]["reaction_time"]["baseline_mean_median_rt_ms"])
        # Slower performance without reduced sleep -> attention, never rest.
        self.assertEqual(after["status"], "attention_needed")

    def test_cortisol_alone_never_changes_status(self):
        before = analyze_alertness("AST-S01", MISSION, db_path=self.db)["status"]
        sa.add_cortisol_measurement("AST-S01", MISSION, "2026-10-15T07:40:00Z", 95.0, "nmol/L",
                                    "saliva", db_path=self.db)
        after = analyze_alertness("AST-S01", MISSION, db_path=self.db)
        self.assertEqual(after["status"], before)
        self.assertTrue(any(o["kind"] == "cortisol" for o in after["supporting_observations"]))

    def test_new_manual_log_is_reflected(self):
        now = datetime.now(timezone.utc).replace(microsecond=0, second=0)
        sa.add_sleep_session("AST-S03", MISSION, now - timedelta(hours=9),
                             now - timedelta(hours=1), data_origin="manual",
                             estimated_sleep_minutes=450, db_path=self.db)
        result = analyze_alertness("AST-S03", MISSION, db_path=self.db)
        # Evaluation point moved to the new log; the old assessments are no longer recent.
        self.assertEqual(result["evaluated_at_utc"], (now - timedelta(hours=1)).strftime(
            sa.UTC_FORMAT))
        self.assertNotEqual(result["status"], "rest_recommended")
        self.assertTrue(any("reaction-time assessment within" in i
                            for i in result["data_quality_issues"]))


class NightGroupingTests(unittest.TestCase):
    def test_wearable_preferred_and_poor_signal_unusable(self):
        def session(sid, origin, quality, estimate, bed="2026-10-01T22:00:00Z",
                    wake="2026-10-02T06:00:00Z"):
            return {"sleep_session_id": sid, "sleep_type": "main", "bedtime_utc": bed,
                    "wake_time_utc": wake, "time_in_bed_minutes": 480.0, "data_origin": origin,
                    "sensor_quality": quality, "estimated_sleep_minutes": estimate,
                    "is_synthetic": False}
        nights = build_nights([session("M", "manual", None, 420),
                               session("W", "wearable", "good", 400, bed="2026-10-01T22:10:00Z")])
        self.assertEqual(len(nights), 1)
        self.assertEqual(nights[0]["night_id"], "W")
        self.assertEqual(nights[0]["estimated_sleep_minutes"], 400)
        poor = build_nights([session("P", "wearable", "poor", 380)])
        self.assertFalse(poor[0]["usable"])
        self.assertIsNone(poor[0]["estimated_sleep_minutes"])
        manual_only_bed = build_nights([session("B", "manual", None, None)])
        self.assertFalse(manual_only_bed[0]["usable"])   # time in bed is not sleep


class EmptyProfileTests(unittest.TestCase):
    def test_no_records_is_insufficient(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "empty.db"
            with redirect_stdout(StringIO()):
                initialize_database(db)
            with closing(get_connection(db)) as conn, conn:
                conn.execute("INSERT INTO astronauts (astronaut_id) VALUES ('AST-X')")
                conn.execute("INSERT INTO missions (mission_id) VALUES ('M-X')")
            result = analyze_alertness("AST-X", "M-X", db_path=db)
            self.assertEqual(result["status"], "insufficient_data")
            self.assertIsNone(result["evaluated_at_utc"])
            self.assertFalse(result["is_synthetic_input"])


if __name__ == "__main__":
    unittest.main()
