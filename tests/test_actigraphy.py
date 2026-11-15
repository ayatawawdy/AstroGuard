"""Actiwatch-compatible import, heuristic scoring and synthetic actigraphy.

The fixture reproduces the STRUCTURE of an "Actiware Export File (Version
05.00)" as found in the public Zenodo 4961281 dataset (header block, a marker
table with its own "Line" header, then the "Epoch-by-Epoch Data" section with
trailing commas and "NaN" cells). Its VALUES are made up for testing.
"""

import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

from backend import actigraphy as ag
from backend import sleep_alertness as sa
from backend.alertness_analysis import analyze_alertness
from backend.database import get_connection, initialize_database
from test_alertness_analysis import MISSION, demo_database

REPO = Path(__file__).resolve().parent.parent
ARCHIVE = REPO / "data" / "external" / "zenodo_4961281" / "Supplementary_Material_Final.zip"


def actiware_fixture(*, columns="full", with_offset=True, day_first_example=True):
    """One night, 60-s epochs, local time UTC+10, from 20:00 to 08:00."""
    lines = ['"Actiware Export File  (Version 05.00 )"',
             '"Filename:","C:\\\\private\\\\path\\\\T0001.csv"', "",
             '"-------------------- Subject Properties--------------------"',
             '"Identity:","T0001 Acti1 Week1"', '"Gender:","Female"', "",
             '"----------------- Actiwatch Data Properties ----------------"']
    if with_offset:
        lines.append('"Time Zone Offset:","+10:00","hours:minutes"')
    lines += ['"Epoch Length:","60","seconds","(1 minute)"',
              '"Wake Threshold Value:","40.00","activity counts"', "",
              '"Line","Date","Time","Marker","Interval Status",',
              '"1","20/05/2017","9:00:00 PM","1","ACTIVE",', "",
              '"-------------------- Epoch-by-Epoch Data -------------------"', ""]
    full = ["Line", "Date", "Time", "Off-Wrist Status", "Activity", "Marker", "White Light",
            "Red Light", "Green Light", "Blue Light", "Sleep/Wake", "Interval Status"]
    header = full if columns == "full" else ["Line", "Date", "Time", "Activity"]
    lines.append(",".join(f'"{c}"' for c in header) + ",")
    lines.append("")
    start = datetime(2017, 5, 20 if day_first_example else 5, 20, 0)
    for i in range(12 * 60):
        moment = start + timedelta(minutes=i)
        local = moment.strftime("%H:%M")
        if "22:30" <= local < "22:45" or "06:30" <= local < "06:45":
            status, activity, sleep_wake = "REST", 30, 1
        elif local >= "22:45" or local < "06:30":
            status, activity, sleep_wake = "REST-S", 0, 0
            if local in ("02:00", "02:01", "02:02"):
                activity, sleep_wake = 120, 1
        else:
            status, activity, sleep_wake = "ACTIVE", 250, 1
        off, light = "0", "150.00" if status == "ACTIVE" else "0.00"
        if "07:55" <= local < "12:00":   # watch removed for the last 5 morning minutes
            status, activity, sleep_wake, off, light = "EXCLUDED", "NaN", "NaN", "1", "NaN"
        date_text = moment.strftime("%d/%m/%Y")
        time_text = moment.strftime("%I:%M:%S %p").lstrip("0")
        values = {"Line": i + 1, "Date": date_text, "Time": time_text, "Off-Wrist Status": off,
                  "Activity": activity, "Marker": 0, "White Light": light, "Red Light": "NaN",
                  "Green Light": "NaN", "Blue Light": "NaN", "Sleep/Wake": sleep_wake,
                  "Interval Status": status}
        lines.append(",".join(f'"{values[c]}"' for c in header) + ",")
    return "\r\n".join(lines) + "\r\n"


class ParserTests(unittest.TestCase):
    def test_parses_actiware_epochs_to_utc(self):
        parsed = ag.parse_actiware_csv(actiware_fixture())
        meta, epochs = parsed["metadata"], parsed["epochs"]
        self.assertEqual(len(epochs), 720)
        self.assertEqual(meta["epoch_seconds"], 60)
        self.assertEqual(meta["subject_code"], "T0001")
        self.assertEqual(epochs[0]["epoch_start_utc"], "2017-05-20T10:00:00Z")   # 20:00 +10:00
        self.assertEqual(epochs[0]["interval_status"], "active")
        self.assertEqual(epochs[0]["activity_count"], 250.0)
        self.assertEqual(epochs[0]["white_light_lux"], 150.0)
        last = epochs[-1]
        self.assertEqual((last["off_wrist"], last["activity_count"], last["device_sleep_wake"],
                          last["interval_status"]), (1, None, None, "excluded"))
        self.assertIn("rest_sleep", {e["interval_status"] for e in epochs})
        # Personal header fields are not carried into metadata.
        flat = repr(meta)
        self.assertNotIn("Female", flat)
        self.assertNotIn("private", flat)

    def test_minimal_columns_supported(self):
        parsed = ag.parse_actiware_csv(actiware_fixture(columns="minimal"))
        epoch = parsed["epochs"][100]
        self.assertIsNone(epoch["off_wrist"])
        self.assertIsNone(epoch["device_sleep_wake"])
        self.assertIsNone(epoch["interval_status"])
        self.assertEqual(parsed["metadata"]["columns"], ["Line", "Date", "Time", "Activity"])

    def test_invalid_inputs_rejected(self):
        with self.assertRaisesRegex(ValueError, "Time Zone Offset"):
            ag.parse_actiware_csv(actiware_fixture(with_offset=False))
        parsed = ag.parse_actiware_csv(actiware_fixture(with_offset=False), utc_offset="+00:00")
        self.assertEqual(parsed["epochs"][0]["epoch_start_utc"], "2017-05-20T20:00:00Z")
        with self.assertRaisesRegex(ValueError, "Ambiguous date order"):
            ag.parse_actiware_csv(actiware_fixture(day_first_example=False))
        self.assertEqual(len(ag.parse_actiware_csv(actiware_fixture(day_first_example=False),
                                                   date_order="dmy")["epochs"]), 720)
        no_activity = actiware_fixture().replace('"Activity",', '"Movement",')
        with self.assertRaisesRegex(ValueError, "Activity"):
            ag.parse_actiware_csv(no_activity)
        negative = actiware_fixture().replace('"250"', '"-5"', 1)
        with self.assertRaisesRegex(ValueError, "non-negative"):
            ag.parse_actiware_csv(negative)
        with self.assertRaises(ValueError):
            ag.parse_actiware_csv("not,an,export\n1,2,3\n")


class ScoringTests(unittest.TestCase):
    def test_heuristic_scoring(self):
        scores = ag.score_sleep_wake([0, 0, 0, 0, 0, 300, 300, 300, None, 0], 60)
        self.assertEqual(scores[:3], ["sleep"] * 3)
        self.assertEqual(scores[5:8], ["wake"] * 3)
        self.assertIsNone(scores[8])
        with self.assertRaises(ValueError):
            ag.score_sleep_wake([0], 15)

    def test_sleep_period_from_device_intervals(self):
        parsed = ag.parse_actiware_csv(actiware_fixture())
        [period] = ag.derive_sleep_periods(parsed["epochs"], 60)
        self.assertEqual(period["bedtime_utc"], "2017-05-20T12:30:00Z")    # 22:30 local
        self.assertEqual(period["wake_time_utc"], "2017-05-20T20:45:00Z")  # 06:45 local
        self.assertEqual(period["time_in_bed_minutes"], 495)
        self.assertEqual(period["estimated_sleep_minutes"], 465 - 3)  # REST-S minus 3 wake min
        self.assertEqual(period["sleep_type"], "main")
        self.assertEqual(period["sensor_quality"], "good")
        self.assertIn("device REST intervals", period["method"])

    def test_sleep_period_from_heuristic_without_intervals(self):
        parsed = ag.parse_actiware_csv(actiware_fixture(columns="minimal"))
        periods = ag.derive_sleep_periods(parsed["epochs"], 60)
        self.assertEqual(len(periods), 1)
        self.assertIn("heuristic", periods[0]["method"])
        self.assertLess(periods[0]["estimated_sleep_minutes"], periods[0]["time_in_bed_minutes"]
                        + 1)


class ImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.db = self.temp / "acti.db"
        with redirect_stdout(StringIO()):
            initialize_database(self.db)

    def test_import_creates_separate_research_profile(self):
        result = ag.import_actiware_file(actiware_fixture().encode("utf-8"),
                                         source_name="T0001.csv", db_path=self.db)
        self.assertEqual(result["astronaut_id"], "RES-T0001")
        self.assertEqual(result["mission_id"], ag.RESEARCH_MISSION_ID)
        self.assertTrue(result["new_recording"])
        [session] = sa.get_sleep_sessions("RES-T0001", db_path=self.db)
        self.assertEqual((session["data_origin"], session["is_synthetic"]), ("wearable", False))
        [recording] = ag.get_recordings("RES-T0001", db_path=self.db)
        self.assertEqual((recording["data_origin"], recording["is_synthetic"],
                          recording["source_format"]), ("actiwatch_export", False, "actiware_csv"))
        with closing(get_connection(self.db)) as conn:
            name = conn.execute("SELECT display_name FROM astronauts WHERE astronaut_id = "
                                "'RES-T0001'").fetchone()[0]
        self.assertIn("not an astronaut", name)
        # No reaction-time data -> the analysis must not invent an alertness conclusion.
        analysis = analyze_alertness("RES-T0001", ag.RESEARCH_MISSION_ID, db_path=self.db)
        self.assertEqual(analysis["status"], "insufficient_data")
        self.assertFalse(analysis["is_synthetic_input"])

    def test_repeated_import_is_idempotent(self):
        source = actiware_fixture().encode("utf-8")
        ag.import_actiware_file(source, source_name="T0001.csv", db_path=self.db)
        again = ag.import_actiware_file(source, source_name="T0001.csv", db_path=self.db)
        self.assertFalse(again["new_recording"])
        self.assertEqual(again["new_sleep_sessions"], 0)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM actigraphy_epochs").fetchone()[0],
                             720)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sleep_sessions").fetchone()[0], 1)

    def test_conflicting_import_into_existing_astronaut_is_atomic(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO astronauts (astronaut_id) VALUES ('AST-X')")
            conn.execute("INSERT INTO missions (mission_id) VALUES ('M-X')")
        sa.add_sleep_session("AST-X", "M-X", "2017-05-20T13:00:00Z", "2017-05-20T20:00:00Z",
                             data_origin="wearable", sensor_quality="good", db_path=self.db)
        with self.assertRaisesRegex(ValueError, "overlaps"):
            ag.import_actiware_file(actiware_fixture(), astronaut_id="AST-X", mission_id="M-X",
                                    db_path=self.db)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM actigraphy_recordings")
                             .fetchone()[0], 0)
        with self.assertRaisesRegex(ValueError, "mission_id is required"):
            ag.import_actiware_file(actiware_fixture(), astronaut_id="AST-X", db_path=self.db)

    @unittest.skipUnless(ARCHIVE.exists(), "Zenodo 4961281 archive not downloaded")
    def test_real_public_dataset_sample(self):
        [result] = ag.import_zenodo_archive(ARCHIVE, limit=1, db_path=self.db)
        self.assertTrue(result["astronaut_id"].startswith("RES-"))
        self.assertGreaterEqual(result["epochs"], 1000)
        sessions = sa.get_sleep_sessions(result["astronaut_id"], db_path=self.db)
        self.assertGreaterEqual(len(sessions), 3)
        self.assertTrue(all(not s["is_synthetic"] for s in sessions))
        mains = [s for s in sessions if s["sleep_type"] == "main" and s["estimated_sleep_minutes"]]
        self.assertTrue(all(60 < s["estimated_sleep_minutes"] <= s["time_in_bed_minutes"]
                            for s in mains))
        [recording] = ag.get_recordings(result["astronaut_id"], db_path=self.db)
        self.assertIn("Zenodo", recording["attribution"])


class SyntheticActigraphyTests(unittest.TestCase):
    def test_synthetic_epochs_are_labeled_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            db = demo_database(Path(temp))
            first = ag.import_synthetic_actigraphy(db_path=db)
            self.assertEqual(set(first.values()), {True})
            self.assertEqual(set(ag.import_synthetic_actigraphy(db_path=db).values()), {False})
            for astronaut in first:
                [recording] = ag.get_recordings(astronaut, db_path=db)
                self.assertTrue(recording["is_synthetic"])
                self.assertEqual(recording["data_origin"], "synthetic")
            # The scenario-4 'unusable' night is mostly off-wrist in the synthetic epochs.
            night = next(s for s in sa.get_sleep_sessions("AST-S04", db_path=db)
                         if s["sensor_quality"] == "unusable")
            epochs = ag.get_epochs("AST-S04", MISSION, night["bedtime_utc"],
                                   night["wake_time_utc"], db_path=db)
            off = sum(e["off_wrist"] == 1 for e in epochs) / len(epochs)
            self.assertGreater(off, 0.8)
            # Asleep epochs are mostly still; the heuristic agrees with the stored estimate.
            s01 = sa.get_sleep_sessions("AST-S01", data_origin="wearable", db_path=db)[3]
            epochs = ag.get_epochs("AST-S01", MISSION, s01["bedtime_utc"], s01["wake_time_utc"],
                                   db_path=db)
            heuristic = ag.score_sleep_wake([e["activity_count"] for e in epochs]).count("sleep")
            self.assertLess(abs(heuristic - s01["estimated_sleep_minutes"]), 60)


if __name__ == "__main__":
    unittest.main()
