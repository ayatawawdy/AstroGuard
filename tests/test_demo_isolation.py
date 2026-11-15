"""Regression: interactive entries can never change the predefined demo scenarios.

Bug reproduced: a browser reaction test taken while Scenario 1 (AST-S01) was
selected was stored on AST-S01 and mixed with its October 2026 synthetic sleep
data, turning Scenario 1 from Normal into Attention Needed.
"""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend import decision_support as ds
from backend import notifications as nt
from backend import sleep_alertness as sa
from test_alertness_analysis import MISSION, demo_database

EXPECTED = {"AST-S01": "normal", "AST-S02": "attention_needed",
            "AST-S03": "rest_recommended", "AST-S04": "insufficient_data"}
LIVE, SESSION = ds.LIVE_ASTRONAUT_ID, ds.LIVE_MISSION_ID


def payload(nonce, started, rts):
    trials = [{"trial_index": i + 1, "interstimulus_interval_ms": 3000.0,
               "reaction_time_ms": rt, "responded_before_stimulus": False}
              for i, rt in enumerate(rts)]
    return {"nonce": nonce, "started_at_utc": started.strftime(sa.UTC_FORMAT),
            "trials": trials, "trial_target": len(trials)}


class DemoIsolationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = demo_database(Path(temp.name))
        ds.ensure_live_profile(db_path=self.db)

    def statuses(self):
        return {a: ds.evaluate_and_notify(a, MISSION, db_path=self.db)["analysis"]["status"]
                for a in EXPECTED}

    def count(self, sql):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql).fetchone()[0]

    def test_reaction_test_on_scenario_1_is_refused(self):
        before = self.count("SELECT COUNT(*) FROM alertness_assessments")
        slow = payload("n1", datetime.now(timezone.utc) - timedelta(minutes=5), [405.0] * 10)
        for astronaut in EXPECTED:
            with self.subTest(astronaut=astronaut), \
                    self.assertRaisesRegex(ValueError, "protected fictional demo scenario"):
                ds.save_browser_assessment(slow, astronaut, MISSION, db_path=self.db)
        with self.assertRaises(ValueError):                       # wrong session on live profile
            ds.save_browser_assessment(slow, LIVE, MISSION, db_path=self.db)
        self.assertEqual(self.count("SELECT COUNT(*) FROM alertness_assessments"), before)
        self.assertEqual(self.statuses(), EXPECTED)

    def test_manual_sleep_log_on_demo_profiles_is_refused(self):
        bed = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=10)
        for astronaut in ("AST-S01", "AST-001"):
            with self.subTest(astronaut=astronaut), self.assertRaises(ValueError):
                ds.save_manual_sleep_log(astronaut, MISSION, bed, bed + timedelta(hours=8),
                                         db_path=self.db)
        self.assertEqual(self.count("SELECT COUNT(*) FROM sleep_sessions WHERE is_synthetic = 0"),
                         0)

    def test_previously_stored_interactive_record_cannot_change_scenario_1(self):
        """Exactly the reported situation: a real Nov-2026 test already stored on AST-S01."""
        baseline_event_ids = {a: (ds.evaluate_and_notify(a, MISSION, db_path=self.db)["event"]
                                  or {}).get("event_id") for a in EXPECTED}
        legacy_id = sa.add_alertness_assessment(
            "AST-S01", MISSION, datetime.now(timezone.utc).replace(microsecond=0)
            - timedelta(hours=1), [{"reaction_time_ms": 405.0}] * 10,
            protocol_version=ds.BROWSER_TEST_PROTOCOL, timing_method="browser_event_timestamp",
            data_origin="app", is_synthetic=False, db_path=self.db)
        outcome = ds.evaluate_and_notify("AST-S01", MISSION, db_path=self.db)
        self.assertEqual(outcome["analysis"]["status"], "normal")
        self.assertIsNone(outcome["event"])
        self.assertEqual(outcome["analysis"]["excluded_record_ids"], [legacy_id])
        self.assertEqual(outcome["analysis"]["input_scope"], "synthetic_only")
        self.assertTrue(outcome["analysis"]["is_synthetic_input"])
        # The record is kept, not deleted.
        self.assertEqual(self.count(f"SELECT COUNT(*) FROM alertness_assessments "
                                    f"WHERE assessment_id = '{legacy_id}'"), 1)
        self.assertEqual(self.statuses(), EXPECTED)
        # Other scenarios keep the identical deduplicated alert identities.
        for astronaut, event_id in baseline_event_ids.items():
            event = ds.evaluate_and_notify(astronaut, MISSION, db_path=self.db)["event"]
            self.assertEqual((event or {}).get("event_id"), event_id)

    def test_live_activity_leaves_all_scenarios_unchanged(self):
        start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=8)
        for i in range(5):
            ds.save_browser_assessment(payload(f"live-{i}", start + timedelta(hours=i),
                                               [400.0 + i] * 10), LIVE, SESSION, db_path=self.db)
        ds.save_manual_sleep_log(LIVE, SESSION, start - timedelta(hours=9), start - timedelta(hours=1),
                                 estimated_sleep_minutes=420, db_path=self.db)
        self.assertEqual(self.statuses(), EXPECTED)
        self.assertEqual(nt.get_notifications("AST-S01", db_path=self.db), [])


class LiveProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = demo_database(Path(temp.name))
        ds.ensure_live_profile(db_path=self.db)
        ds.ensure_live_profile(db_path=self.db)                 # idempotent

    def test_profile_registered_without_any_data(self):
        self.assertEqual(ds.get_profile_kind(LIVE), "live")
        self.assertEqual([m["mission_id"] for m in ds.get_astronaut_missions(LIVE, db_path=self.db)],
                         [SESSION])
        result = ds.evaluate_and_notify(LIVE, SESSION, db_path=self.db)
        self.assertEqual(result["analysis"]["status"], "insufficient_data")
        self.assertEqual(result["analysis"]["input_scope"], "all_records")
        self.assertEqual(sa.get_sleep_sessions(LIVE, db_path=self.db), [])

    def test_live_tests_are_stored_analyzed_and_alerted(self):
        start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=10)
        outcomes = []
        for i, rt in enumerate([250, 255, 248, 252, 450, 470]):
            outcomes.append(ds.save_browser_assessment(
                payload(f"p{i}", start + timedelta(hours=i), [float(rt + k) for k in range(10)]),
                LIVE, SESSION, db_path=self.db))
        stored = sa.get_alertness_assessments(LIVE, db_path=self.db)
        self.assertEqual(len(stored), 6)
        self.assertTrue(all(not a["is_synthetic"] and a["data_origin"] == "app" for a in stored))
        final = outcomes[-1]
        analysis = final["analysis"]
        # Slower than the live profile's OWN baseline; no sleep data exists or is invented.
        self.assertEqual(analysis["status"], "attention_needed")
        self.assertFalse(analysis["is_synthetic_input"])
        self.assertEqual(sa.get_sleep_sessions(LIVE, db_path=self.db), [])
        self.assertTrue(any("sleep" in issue for issue in analysis["data_quality_issues"]))
        event = final["event"]
        self.assertEqual(event["astronaut_id"], LIVE)
        self.assertFalse(event["is_synthetic_input"])
        acked = nt.acknowledge_notification(event["event_id"], LIVE, db_path=self.db)
        self.assertTrue(acked["acknowledged"])
        with self.assertRaises(ValueError):
            nt.acknowledge_notification(event["event_id"], "AST-S01", db_path=self.db)


if __name__ == "__main__":
    unittest.main()
