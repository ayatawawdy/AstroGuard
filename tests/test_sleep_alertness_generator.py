"""Stage 1A: synthetic sleep & alertness scenario generator tests."""

import json
import statistics
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd

from backend import sleep_alertness as sa
from backend.import_sleep_alertness import import_sleep_alertness
from data import generate_sleep_alertness as gen

REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY_CSV = REPO_ROOT / "data" / "synthetic" / "health_measurements.csv"
FILES = ["scenarios.json", "sleep_sessions.csv", "sleep_stages.csv",
         "alertness_assessments.csv", "reaction_time_trials.csv", "cortisol_measurements.csv"]


class GeneratorReproducibilityTests(unittest.TestCase):
    def test_same_seed_produces_identical_files(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            gen.write_dataset(a)
            gen.write_dataset(b)
            for name in FILES:
                with self.subTest(file=name):
                    self.assertEqual((Path(a) / name).read_bytes(), (Path(b) / name).read_bytes())

    def test_different_seed_changes_values(self):
        first, second = gen.generate(1), gen.generate(2)
        self.assertFalse(first["reaction_time_trials"].equals(second["reaction_time_trials"]))

    def test_committed_dataset_matches_generator(self):
        committed = gen.OUTPUT_DIR
        if not (committed / "scenarios.json").exists():
            self.skipTest("Dataset not generated in this checkout")
        with tempfile.TemporaryDirectory() as fresh:
            gen.write_dataset(fresh)
            for name in FILES:
                with self.subTest(file=name):
                    # Ignore CRLF/LF differences introduced by git checkout settings.
                    normalize = lambda p: p.read_bytes().replace(b"\r\n", b"\n")
                    self.assertEqual(normalize(committed / name), normalize(Path(fresh) / name))

    def test_legacy_dataset_untouched(self):
        legacy = pd.read_csv(LEGACY_CSV)
        self.assertEqual(len(legacy), 720)
        self.assertNotEqual(gen.OUTPUT_DIR, LEGACY_CSV.parent)
        legacy_ids = set(legacy["astronaut_id"])
        self.assertFalse(legacy_ids & {s["astronaut_id"] for s in gen.SCENARIOS})
        self.assertNotIn(gen.MISSION_ID, set(legacy["mission_id"]))


class GeneratorContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        temp = Path(cls._temp.name)
        gen.write_dataset(temp / "data")
        cls.db = temp / "scenarios.db"
        with redirect_stdout(StringIO()):
            import_sleep_alertness(temp / "data", db_path=cls.db)
        cls.manifest = json.loads((temp / "data" / "scenarios.json").read_text(encoding="utf-8"))
        cls.frames = gen.generate()

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def sessions(self, astronaut, **kwargs):
        return sa.get_sleep_sessions(astronaut, db_path=self.db, **kwargs)

    def medians(self, astronaut):
        return [a["median_rt_ms"] for a in sa.get_alertness_assessments(astronaut, db_path=self.db)]

    def test_four_labeled_fictional_scenarios(self):
        keys = [s["scenario_key"] for s in self.manifest["astronauts"]]
        self.assertEqual(keys, ["stable_sleep_stable_alertness", "shifted_schedule_no_decline",
                                "reduced_sleep_slower_reactions", "insufficient_or_missing_data"])
        self.assertTrue(self.manifest["is_synthetic"])
        self.assertIn("not clinically validated", self.manifest["disclaimer"])

    def test_every_row_is_synthetic(self):
        for name, frame in self.frames.items():
            if "is_synthetic" in frame:
                with self.subTest(table=name):
                    self.assertTrue(frame["is_synthetic"].all())
        self.assertTrue((self.frames["alertness_assessments"]["timing_method"]
                         == "synthetic_generator").all())

    def test_stages_tile_sessions_and_match_estimate(self):
        for astronaut in ("AST-S01", "AST-S02", "AST-S03", "AST-S04"):
            for session in self.sessions(astronaut, include_stages=True):
                stages = session["sleep_stages"]
                if not stages:
                    continue
                with self.subTest(session=session["sleep_session_id"]):
                    self.assertEqual(stages[0]["start_utc"], session["bedtime_utc"])
                    self.assertEqual(stages[-1]["end_utc"], session["wake_time_utc"])
                    for a, b in zip(stages, stages[1:]):
                        self.assertEqual(a["end_utc"], b["start_utc"])
                    asleep = sum(sa._minutes_between(s["start_utc"], s["end_utc"])
                                 for s in stages if s["stage_type"] != "awake")
                    self.assertEqual(asleep, session["estimated_sleep_minutes"])
                    self.assertLess(asleep, session["time_in_bed_minutes"])

    def test_stable_scenario(self):
        wearable = self.sessions("AST-S01", data_origin="wearable")
        self.assertEqual(len(wearable), gen.NIGHTS)
        self.assertEqual(len(self.sessions("AST-S01", data_origin="manual")), gen.NIGHTS)
        self.assertTrue(all(s["bedtime_utc"][:10] < s["wake_time_utc"][:10] for s in wearable),
                        "main sleep crosses midnight")
        self.assertLess(statistics.pstdev(self.medians("AST-S01")), 15)
        self.assertGreater(len(sa.get_cortisol_measurements("AST-S01", db_path=self.db)), 0)

    def test_shifted_scenario_without_decline(self):
        wearable = self.sessions("AST-S02", data_origin="wearable")
        clock = [int(s["bedtime_utc"][11:13]) * 60 + int(s["bedtime_utc"][14:16]) for s in wearable]
        clock = [(m - 12 * 60) % (24 * 60) for m in clock]  # minutes after noon
        self.assertGreater(clock[-1] - clock[0], 5 * 60)
        durations = [s["time_in_bed_minutes"] for s in wearable]
        self.assertLess(max(durations) - min(durations), 90)
        medians = self.medians("AST-S02")
        self.assertLess(statistics.mean(medians[-5:]) - statistics.mean(medians[:5]), 15)

    def test_reduced_sleep_with_slower_reactions(self):
        main = self.sessions("AST-S03", data_origin="wearable", sleep_type="main")
        before = statistics.mean(s["time_in_bed_minutes"] for s in main[:5])
        after = statistics.mean(s["time_in_bed_minutes"] for s in main[5:])
        self.assertLess(after, before - 120)
        self.assertEqual(len(self.sessions("AST-S03", sleep_type="nap")), 3)
        assessments = sa.get_alertness_assessments("AST-S03", db_path=self.db)
        self.assertGreater(statistics.mean(a["median_rt_ms"] for a in assessments[-4:]),
                           statistics.mean(a["median_rt_ms"] for a in assessments[:5]) + 25)
        self.assertGreater(sum(a["lapse_count"] for a in assessments[-4:]),
                           sum(a["lapse_count"] for a in assessments[:5]))

    def test_insufficient_or_missing_data_scenario(self):
        sessions = self.sessions("AST-S04", include_stages=True)
        nights = {s["sleep_session_id"].split("-")[2] for s in sessions}
        self.assertLessEqual(len(nights), gen.NIGHTS // 2)  # at least half the nights missing
        unusable = [s for s in sessions if s["sensor_quality"] == "unusable"]
        self.assertEqual(len(unusable), 1)
        self.assertIsNone(unusable[0]["estimated_sleep_minutes"])
        self.assertTrue(any(s["stage_data_status"] == "unavailable"
                            and s["data_origin"] == "wearable" for s in sessions))
        self.assertTrue(any(s["data_origin"] == "manual" and s["estimated_sleep_minutes"] is None
                            for s in sessions))
        assessments = sa.get_alertness_assessments("AST-S04", db_path=self.db)
        self.assertLess(len(assessments), 5)
        self.assertIn("incomplete", {a["completion_status"] for a in assessments})
        self.assertIn(None, {a["self_reported_fatigue"] for a in assessments})
        self.assertEqual(sa.get_cortisol_measurements("AST-S04", db_path=self.db), [])


if __name__ == "__main__":
    unittest.main()
