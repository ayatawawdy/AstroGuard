"""Persistent notifications: creation, deduplication, acknowledgment, history, isolation."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
from pathlib import Path

from backend import notifications as nt
from backend import sleep_alertness as sa
from backend.decision_support import evaluate_and_notify, get_astronaut_missions
from test_alertness_analysis import MISSION, demo_database


class NotificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = demo_database(Path(temp.name))

    def evaluate(self, astronaut, **kwargs):
        return evaluate_and_notify(astronaut, MISSION, db_path=self.db, **kwargs)

    def rows(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT COUNT(*) FROM rest_notification_events").fetchone()[0]

    def test_rest_recommended_creates_persistent_event(self):
        outcome = self.evaluate("AST-S03")
        event = outcome["event"]
        self.assertTrue(outcome["new_event"])
        self.assertEqual(event["alertness_status"], "rest_recommended")
        self.assertEqual(event["title"], "REST RECOMMENDED")
        self.assertEqual(event["severity"], "warning")
        self.assertFalse(event["acknowledged"])
        self.assertTrue(event["is_synthetic_input"])
        self.assertEqual(event["source_assessment_id"],
                         outcome["analysis"]["evidence"]["recent_assessment_ids"][-1])
        self.assertTrue(event["evidence"]["supporting_observations"])
        self.assertEqual(nt.count_unacknowledged("AST-S03", db_path=self.db), 1)

    def test_normal_and_insufficient_create_no_event(self):
        for astronaut in ("AST-S01", "AST-S04"):
            with self.subTest(astronaut=astronaut):
                outcome = self.evaluate(astronaut)
                self.assertIsNone(outcome["event"])
                self.assertFalse(outcome["new_event"])
        self.assertEqual(self.rows(), 0)

    def test_repeated_evaluation_is_deduplicated(self):
        first = self.evaluate("AST-S03")["event"]
        for task in ("routine_monitoring", "safety_critical_operation", "routine_monitoring"):
            again = self.evaluate("AST-S03", task_context=task)   # e.g. Streamlit reruns
            self.assertFalse(again["new_event"])
            self.assertEqual(again["event"]["event_id"], first["event_id"])
        self.assertEqual(self.rows(), 1)

    def test_new_evidence_creates_new_event(self):
        first = self.evaluate("AST-S03")["event"]
        slow = [{"reaction_time_ms": rt} for rt in (420, 480, 530, None, 510, 450, 495, 600)]
        # New *scenario* evidence (synthetic): demo profiles are evaluated on synthetic
        # records only, so a real entry would be excluded (see test_demo_isolation).
        sa.add_alertness_assessment("AST-S03", MISSION, "2026-10-15T09:00:00Z", slow,
                                    protocol_version="test", data_origin="synthetic",
                                    is_synthetic=True, timing_method="synthetic_generator",
                                    db_path=self.db)
        second = self.evaluate("AST-S03")
        self.assertTrue(second["new_event"])
        self.assertNotEqual(second["event"]["event_id"], first["event_id"])
        history = nt.get_notifications("AST-S03", db_path=self.db)
        self.assertEqual([e["event_id"] for e in history],
                         sorted([first["event_id"], second["event"]["event_id"]],
                                key=lambda i: i != second["event"]["event_id"]))

    def test_history_is_newest_first_within_the_same_second(self):
        """Regression: alerts created in the same second were ordered by hash-based ID."""
        created = []
        with patch.object(nt, "_now", return_value="2026-11-15T12:00:00Z"):
            created.append(self.evaluate("AST-S03")["event"]["event_id"])
            for i in range(5):
                slow = [{"reaction_time_ms": 420.0 + 10 * i + k} for k in range(8)]
                sa.add_alertness_assessment(
                    "AST-S03", MISSION, f"2026-10-15T{9 + i:02d}:00:00Z", slow,
                    protocol_version="test", data_origin="synthetic", is_synthetic=True,
                    timing_method="synthetic_generator", db_path=self.db)
                outcome = self.evaluate("AST-S03")
                self.assertTrue(outcome["new_event"])
                created.append(outcome["event"]["event_id"])
        history = [e["event_id"] for e in nt.get_notifications("AST-S03", db_path=self.db)]
        self.assertEqual(history, list(reversed(created)))

    def test_acknowledgment_persists_and_keeps_history(self):
        event = self.evaluate("AST-S03")["event"]
        acked = nt.acknowledge_notification(event["event_id"], "AST-S03",
                                            acknowledged_by="Flight surgeon (demo)",
                                            db_path=self.db)
        self.assertTrue(acked["acknowledged"])
        self.assertIsNotNone(acked["acknowledged_utc"])
        self.assertEqual(acked["acknowledged_by"], "Flight surgeon (demo)")
        # Idempotent: the first acknowledgment time is kept.
        again = nt.acknowledge_notification(event["event_id"], "AST-S03", db_path=self.db)
        self.assertEqual(again["acknowledged_utc"], acked["acknowledged_utc"])
        # A fresh read (new connection) still shows the acknowledged event in history.
        history = nt.get_notifications(db_path=self.db)
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["acknowledged"])
        self.assertEqual(nt.count_unacknowledged(db_path=self.db), 0)
        self.assertEqual(nt.get_notifications(unacknowledged_only=True, db_path=self.db), [])
        # Re-evaluating the same evidence does not resurrect an unacknowledged duplicate.
        outcome = self.evaluate("AST-S03")
        self.assertFalse(outcome["new_event"])
        self.assertTrue(outcome["event"]["acknowledged"])

    def test_cannot_acknowledge_another_astronauts_event(self):
        event = self.evaluate("AST-S03")["event"]
        with self.assertRaises(ValueError):
            nt.acknowledge_notification(event["event_id"], "AST-S01", db_path=self.db)
        with self.assertRaises(ValueError):
            nt.acknowledge_notification("ALERT-missing", "AST-S03", db_path=self.db)
        self.assertFalse(nt.get_notifications("AST-S03", db_path=self.db)[0]["acknowledged"])

    def test_history_isolated_by_astronaut_and_mission(self):
        self.evaluate("AST-S02")
        self.evaluate("AST-S03")
        self.assertEqual({e["astronaut_id"] for e in nt.get_notifications(db_path=self.db)},
                         {"AST-S02", "AST-S03"})
        self.assertEqual([e["astronaut_id"] for e in nt.get_notifications(
            "AST-S02", db_path=self.db)], ["AST-S02"])
        self.assertEqual(nt.get_notifications("AST-S03", mission_id="MISSION-DEMO-01",
                                              db_path=self.db), [])

    def test_missions_include_non_legacy_records(self):
        self.assertEqual(get_astronaut_missions("AST-S03", db_path=self.db),
                         [{"mission_id": MISSION,
                           "mission_name": "Fictional Sleep & Alertness Demo Mission"}])
        self.assertEqual(get_astronaut_missions("AST-NOPE", db_path=self.db), [])


if __name__ == "__main__":
    unittest.main()
