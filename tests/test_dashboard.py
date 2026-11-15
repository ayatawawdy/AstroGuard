"""End-to-end Mission Control dashboard tests (Streamlit AppTest).

The app runs in a subprocess with ASTROGUARD_DB_PATH pointing at a temporary
database, so the real backend/astroguard.db is never opened. The flow covers
scenario selection, persistent alerts, deduplication across reruns and
sessions, acknowledgment, task context and a manual sleep log.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PREFIX = "DASHBOARD_RESULT="

SCRIPT = r'''
import json, re, sys
from datetime import date, time, timedelta, datetime, timezone
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = sys.argv[1]

def state(at):
    banner = next((h.proto.body for h in at.get("html") if "Suggested action" in h.proto.body), "")
    head = re.search(r'letter-spacing:1px;">(.*?)</div>', banner)
    return {
        "exceptions": [e.message for e in at.exception],
        "errors": [e.value for e in at.error],
        "headline": head.group(1) if head else None,
        "banner": banner,
        "metrics": {m.label: m.value for m in at.metric},
        "ack_buttons": [b.key for b in at.button if (b.key or "").startswith("ack_banner_")],
        "success": [s.value for s in at.success],
        "charts": len(at.get("plotly_chart")),
        "selected": at.selectbox(key="astronaut_select").value,
        "infos": [i.value for i in at.info],
        "captions": [c.value for c in at.caption],
        "has_log_form": any(b.key == "log_submit" for b in at.button),
    }

out = {}
at = AppTest.from_file(APP, default_timeout=180).run()
out["initial"] = state(at)
for aid in ["AST-S01", "AST-S02", "AST-S03", "AST-S04"]:
    at.button(key=f"scenario_{aid}").click().run()
    out[aid] = state(at)

at.button(key="scenario_AST-S03").click().run()
at.run(); at.run()                                   # plain reruns
out["S03_rerun"] = state(at)
at.selectbox(key="task_context").select("safety_critical_operation").run()
out["S03_safety"] = state(at)
ack_key = out["S03_safety"]["ack_buttons"][0]
at.button(key=ack_key).click().run()
out["after_ack"] = state(at)

st.cache_resource.clear()                            # reopen the dashboard
second = AppTest.from_file(APP, default_timeout=180).run()
second.button(key="scenario_AST-S03").click().run()
out["reopened"] = state(second)

second.button(key="scenario_AST-S01").click().run()
out["S01_protected"] = state(second)                # demo profile: no entry forms
second.button(key="live_profile").click().run()
out["live_initial"] = state(second)
today = datetime.now(timezone.utc).date()
second.date_input(key="log_bed_date").set_value(today - timedelta(days=2))
second.time_input(key="log_bed_time").set_value(time(22, 15))
second.date_input(key="log_wake_date").set_value(today - timedelta(days=1))
second.time_input(key="log_wake_time").set_value(time(6, 45))
second.checkbox(key="log_estimate_known").check()
second.number_input(key="log_estimate_hours").set_value(7.0)
second.text_input(key="log_notes").input("demo entry")
second.button(key="log_submit").click().run()
out["after_log"] = state(second)
second.button(key="scenario_AST-S01").click().run()
out["S01_after_live_entry"] = state(second)
print("DASHBOARD_RESULT=" + json.dumps(out))
'''


class DashboardEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.db = Path(cls._temp.name) / "dashboard.db"
        env = {**os.environ, "ASTROGUARD_DB_PATH": str(cls.db), "PYTHONIOENCODING": "utf-8"}
        completed = subprocess.run(
            [sys.executable, "-c", SCRIPT, str(REPO / "app.py")], cwd=REPO, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        cls.output = completed.stdout + completed.stderr
        lines = [l for l in completed.stdout.splitlines() if l.startswith(PREFIX)]
        cls.r = json.loads(lines[-1][len(PREFIX):]) if lines else None

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def setUp(self):
        self.assertIsNotNone(self.r, f"Dashboard harness failed:\n{self.output[-4000:]}")

    def query(self, sql):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql).fetchall()

    def assert_clean(self, run):
        self.assertEqual(run["exceptions"], [])
        self.assertEqual(run["errors"], [])

    def test_startup_without_database_selects_scenario_1(self):
        self.assert_clean(self.r["initial"])
        self.assertEqual(self.r["initial"]["selected"], "AST-S01")
        self.assertIn("NORMAL", self.r["initial"]["headline"])
        self.assertEqual(self.query("SELECT COUNT(*) FROM measurements"), [(720,)])

    def test_four_scenarios_through_the_pipeline(self):
        expected = {"AST-S01": ("NORMAL", "Normal"),
                    "AST-S02": ("ATTENTION NEEDED", "Attention Needed"),
                    "AST-S03": ("REST RECOMMENDED", "Rest Recommended"),
                    "AST-S04": ("INSUFFICIENT DATA", "Insufficient Data")}
        for astronaut, (headline, label) in expected.items():
            with self.subTest(astronaut=astronaut):
                run = self.r[astronaut]
                self.assert_clean(run)
                self.assertEqual(run["selected"], astronaut)
                self.assertIn(headline, run["headline"])
                self.assertEqual(run["metrics"]["Alertness status"], label)
                self.assertGreaterEqual(run["charts"], 2)
                self.assertIn("SYNTHETIC DEMO DATA", run["banner"])

    def test_rest_recommended_shows_evidence_and_acknowledge(self):
        run = self.r["AST-S03"]
        self.assertIn("Potential reduction in alertness detected", run["banner"])
        self.assertIn("personal baseline", run["banner"])
        self.assertIn("Data observed up to", run["banner"])
        self.assertIn("Analysis run", run["banner"])
        self.assertEqual(len(run["ack_buttons"]), 1)
        self.assertEqual(run["metrics"]["Unacknowledged alerts"], "1")
        self.assertEqual(self.r["AST-S01"]["ack_buttons"], [])
        self.assertEqual(self.r["AST-S04"]["ack_buttons"], [])

    def test_alerts_not_duplicated_across_reruns_and_sessions(self):
        rows = self.query("SELECT astronaut_id, alertness_status FROM rest_notification_events "
                          "ORDER BY astronaut_id")
        self.assertEqual(rows, [("AST-S02", "attention_needed"), ("AST-S03", "rest_recommended")])
        self.assertEqual(self.r["S03_rerun"]["ack_buttons"], self.r["AST-S03"]["ack_buttons"])

    def test_task_context_changes_recommendation_not_status(self):
        run = self.r["S03_safety"]
        self.assertIn("REST RECOMMENDED", run["headline"])
        self.assertIn("Safety-Critical Operation", run["banner"])
        self.assertIn("does not pause or terminate the operation", run["banner"])

    def test_acknowledgment_saved_and_kept_in_history(self):
        self.assert_clean(self.r["after_ack"])
        self.assertEqual(self.r["after_ack"]["ack_buttons"], [])
        self.assertEqual(self.r["after_ack"]["metrics"]["Unacknowledged alerts"], "0")
        self.assertTrue(any("acknowledged" in s for s in self.r["after_ack"]["success"]))
        [(acked, when)] = self.query("SELECT acknowledged, acknowledged_utc FROM "
                                     "rest_notification_events WHERE astronaut_id = 'AST-S03'")
        self.assertEqual(acked, 1)
        self.assertIsNotNone(when)
        # Reopened dashboard: still acknowledged, still one S03 event, status unchanged.
        reopened = self.r["reopened"]
        self.assert_clean(reopened)
        self.assertIn("REST RECOMMENDED", reopened["headline"])
        self.assertEqual(reopened["ack_buttons"], [])
        self.assertEqual(self.query("SELECT COUNT(*) FROM rest_notification_events "
                                    "WHERE astronaut_id = 'AST-S03'"), [(1,)])

    def test_demo_profile_offers_no_entry_forms(self):
        run = self.r["S01_protected"]
        self.assert_clean(run)
        self.assertFalse(run["has_log_form"])
        self.assertTrue(any("Live test profile" in i for i in run["infos"]), run["infos"])
        self.assertTrue(any("Protected demo scenario" in c for c in run["captions"]))

    def test_live_profile_is_separate_and_labeled(self):
        run = self.r["live_initial"]
        self.assert_clean(run)
        self.assertEqual(run["selected"], "LIVE-USER-01")
        self.assertTrue(run["has_log_form"])
        self.assertIn("INSUFFICIENT DATA", run["headline"])   # no data is fabricated
        self.assertIn("REAL / USER-ENTERED DATA", run["banner"])
        self.assertNotIn("SYNTHETIC DEMO DATA", run["banner"])

    def test_manual_sleep_log_saved_to_live_profile_only(self):
        run = self.r["after_log"]
        self.assert_clean(run)
        self.assertTrue(any("Sleep log" in s and "saved" in s for s in run["success"]), run)
        self.assertEqual(self.query("SELECT COUNT(*) FROM sleep_sessions WHERE is_synthetic = 0 "
                                    "AND astronaut_id LIKE 'AST-S%'"), [(0,)])
        rows = self.query("SELECT data_origin, is_synthetic, estimated_sleep_minutes, notes, "
                          "bedtime_utc FROM sleep_sessions WHERE astronaut_id = 'LIVE-USER-01' "
                          "AND notes LIKE 'Dashboard entry%'")
        self.assertEqual(len(rows), 1)
        origin, synthetic, estimate, notes, bedtime = rows[0]
        self.assertEqual((origin, synthetic, estimate), ("manual", 0, 420.0))
        expected_bed = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
        self.assertEqual(bedtime, f"{expected_bed}T22:15:00Z")
        # One real night and no tests: honestly insufficient (nothing fabricated).
        self.assertIn("INSUFFICIENT DATA", run["headline"])
        # Regression: the predefined Scenario 1 outcome is unchanged by live entries.
        after = self.r["S01_after_live_entry"]
        self.assert_clean(after)
        self.assertIn("NORMAL", after["headline"])
        self.assertEqual(after["metrics"]["Alertness status"], "Normal")


class BrowserAssessmentTests(unittest.TestCase):
    def test_browser_result_saved_once_and_reevaluated(self):
        from backend import decision_support as ds
        from backend import sleep_alertness as sa
        from test_alertness_analysis import MISSION, demo_database

        with tempfile.TemporaryDirectory() as temp:
            db = demo_database(Path(temp))
            ds.ensure_live_profile(db_path=db)
            live, session = ds.LIVE_ASTRONAUT_ID, ds.LIVE_MISSION_ID
            now = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
            payload = {"nonce": "abc-123", "started_at_utc": "2026-10-15T10:05:00Z",
                       "trial_target": 4,
                       "trials": [{"trial_index": 1, "interstimulus_interval_ms": 3000,
                                   "reaction_time_ms": 251.3, "responded_before_stimulus": False},
                                  {"trial_index": 2, "interstimulus_interval_ms": 2500,
                                   "reaction_time_ms": None, "responded_before_stimulus": True},
                                  {"trial_index": 3, "interstimulus_interval_ms": 4000,
                                   "reaction_time_ms": None, "responded_before_stimulus": False},
                                  {"trial_index": 4, "interstimulus_interval_ms": 5000,
                                   "reaction_time_ms": 610.0, "responded_before_stimulus": False}]}
            first = ds.save_browser_assessment(payload, live, session, now=now, db_path=db)
            again = ds.save_browser_assessment(payload, live, session, now=now, db_path=db)
            self.assertEqual(first["assessment_id"], again["assessment_id"])
            [stored] = [a for a in sa.get_alertness_assessments(live, db_path=db,
                                                               include_trials=True)
                        if a["assessment_id"] == first["assessment_id"]]
            self.assertEqual(stored["assessment_utc"], "2026-10-15T10:00:00Z")  # clamped
            self.assertEqual((stored["timing_method"], stored["data_origin"],
                              stored["is_synthetic"]), ("browser_event_timestamp", "app", False))
            self.assertEqual([t["outcome"] for t in stored["trials"]],
                             ["valid", "false_start", "lapse", "lapse"])
            # A single live test has no personal baseline yet: honestly insufficient.
            self.assertEqual(first["analysis"]["status"], "insufficient_data")
            self.assertTrue(any("1 completed reaction-time assessment" in issue
                                for issue in first["analysis"]["data_quality_issues"]))
            with self.assertRaises(ValueError):
                ds.save_browser_assessment({"trials": []}, live, session, db_path=db)
            # The demo scenario profile itself is refused and stays untouched.
            with self.assertRaisesRegex(ValueError, "protected fictional demo scenario"):
                ds.save_browser_assessment(dict(payload, nonce="other"), "AST-S01", MISSION,
                                           now=now, db_path=db)
            self.assertTrue(all(a["is_synthetic"] for a in
                                sa.get_alertness_assessments("AST-S01", db_path=db)))


if __name__ == "__main__":
    unittest.main()
