"""Stage 1A: sleep, alertness and cortisol storage tests.

Every test uses its own temporary SQLite file; the real backend/astroguard.db
is never opened.
"""

import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from backend import services
from backend import sleep_alertness as sa
from backend.database import get_connection, initialize_database
from backend.import_sleep_alertness import TABLES, import_sleep_alertness
from data.generate_sleep_alertness import write_dataset

NEW_TABLES = {"sleep_sessions", "sleep_stages", "alertness_assessments",
              "reaction_time_trials", "cortisol_measurements"}
LEGACY_TABLES = {"astronauts", "missions", "measurements"}

# Exact pre-Stage-1A schema, used to simulate upgrading an existing database.
LEGACY_DDL = """
CREATE TABLE astronauts (astronaut_id TEXT PRIMARY KEY, display_name TEXT);
CREATE TABLE missions (mission_id TEXT PRIMARY KEY, mission_name TEXT);
CREATE TABLE measurements (
    measurement_id TEXT PRIMARY KEY, astronaut_id TEXT NOT NULL,
    mission_id TEXT NOT NULL, timestamp_utc TEXT NOT NULL,
    metric_type TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('manual', 'device', 'synthetic')),
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic IN (0, 1)), context TEXT,
    CHECK ((source = 'synthetic' AND is_synthetic = 1)
           OR (source != 'synthetic' AND is_synthetic = 0)),
    FOREIGN KEY (astronaut_id) REFERENCES astronauts(astronaut_id),
    FOREIGN KEY (mission_id) REFERENCES missions(mission_id));
CREATE INDEX idx_measurements_astronaut_time ON measurements (astronaut_id, timestamp_utc);
CREATE INDEX idx_measurements_metric ON measurements (metric_type);
"""

BED = "2026-10-01T22:45:00Z"
WAKE = "2026-10-02T06:15:00Z"   # crosses midnight; 450 minutes in bed


def quiet(func, *args, **kwargs):
    """Call ``func`` while discarding initialize_database's console output."""
    with redirect_stdout(StringIO()):
        return func(*args, **kwargs)


class TempDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp_dir = Path(temp.name)
        self.db = self.temp_dir / "test.db"
        quiet(initialize_database, self.db)
        with closing(get_connection(self.db)) as conn, conn:
            conn.executemany("INSERT INTO astronauts (astronaut_id) VALUES (?)",
                             [("AST-A",), ("AST-B",)])
            conn.executemany("INSERT INTO missions (mission_id) VALUES (?)",
                             [("M-1",), ("M-2",)])

    def query(self, sql, params=()):
        with closing(get_connection(self.db)) as conn:
            return [tuple(r) for r in conn.execute(sql, params)]

    def count(self, table):
        return self.query(f"SELECT COUNT(*) FROM {table}")[0][0]

    def wearable(self, **overrides):
        kwargs = dict(data_origin="wearable", sensor_quality="good",
                      sensor_coverage_fraction=0.95, estimated_sleep_minutes=410,
                      db_path=self.db)
        kwargs.update(overrides)
        astronaut = kwargs.pop("astronaut_id", "AST-A")
        mission = kwargs.pop("mission_id", "M-1")
        bed = kwargs.pop("bedtime_utc", BED)
        wake = kwargs.pop("wake_time_utc", WAKE)
        return sa.add_sleep_session(astronaut, mission, bed, wake, **kwargs)

    def manual(self, **overrides):
        kwargs = dict(data_origin="manual", db_path=self.db)
        kwargs.update(overrides)
        astronaut = kwargs.pop("astronaut_id", "AST-A")
        mission = kwargs.pop("mission_id", "M-1")
        bed = kwargs.pop("bedtime_utc", "2026-10-01T22:30:00Z")
        wake = kwargs.pop("wake_time_utc", "2026-10-02T06:30:00Z")
        return sa.add_sleep_session(astronaut, mission, bed, wake, **kwargs)

    def assessment(self, trials, **overrides):
        kwargs = dict(protocol_version="test-v1", timing_method="software_monotonic_clock",
                      db_path=self.db)
        kwargs.update(overrides)
        astronaut = kwargs.pop("astronaut_id", "AST-A")
        mission = kwargs.pop("mission_id", "M-1")
        when = kwargs.pop("assessment_utc", "2026-10-02T07:30:00Z")
        return sa.add_alertness_assessment(astronaut, mission, when, trials, **kwargs)


# ---------------------------------------------------------------------------
# Schema, backward compatibility, idempotent initialization
# ---------------------------------------------------------------------------

class SchemaTests(TempDatabaseTestCase):
    def test_new_and_legacy_tables_exist(self):
        tables = {r[0] for r in self.query("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(NEW_TABLES | LEGACY_TABLES <= tables)

    def test_indexes_created(self):
        indexes = {r[0] for r in self.query("SELECT name FROM sqlite_master WHERE type='index'")}
        for name in ("idx_sleep_sessions_astronaut_mission_time",
                     "idx_alertness_astronaut_mission_time",
                     "idx_cortisol_astronaut_mission_time",
                     "idx_measurements_astronaut_time", "idx_measurements_metric"):
            self.assertIn(name, indexes)

    def test_foreign_keys_declared(self):
        expected = {
            "sleep_sessions": {"astronauts", "missions"},
            "sleep_stages": {"sleep_sessions"},
            "alertness_assessments": {"astronauts", "missions"},
            "reaction_time_trials": {"alertness_assessments"},
            "cortisol_measurements": {"astronauts", "missions", "sleep_sessions"},
        }
        for table, parents in expected.items():
            with self.subTest(table=table):
                refs = {r[2] for r in self.query(f"PRAGMA foreign_key_list({table})")}
                self.assertEqual(refs, parents)

    def test_constraints_enforced_for_raw_sql(self):
        bad_sessions = [
            ("reversed", BED, "2026-10-01T20:00:00Z", "wearable", 0, "good"),
            ("malformed", "not-a-time", WAKE, "wearable", 0, "good"),
            ("synthetic-flag", BED, WAKE, "synthetic", 0, None),
            ("manual-sensor", BED, WAKE, "manual", 0, "good"),
            ("wearable-no-quality", BED, WAKE, "wearable", 0, None),
        ]
        with closing(get_connection(self.db)) as conn:
            for sid, bed, wake, origin, synthetic, quality in bad_sessions:
                with self.subTest(case=sid), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO sleep_sessions (sleep_session_id, astronaut_id, mission_id,"
                        " sleep_type, bedtime_utc, wake_time_utc, data_origin, is_synthetic,"
                        " sensor_quality) VALUES (?, 'AST-A', 'M-1', 'main', ?, ?, ?, ?, ?)",
                        (sid, bed, wake, origin, synthetic, quality))
            with self.assertRaises(sqlite3.IntegrityError):   # unknown astronaut (FK)
                conn.execute(
                    "INSERT INTO sleep_sessions (sleep_session_id, astronaut_id, mission_id,"
                    " sleep_type, bedtime_utc, wake_time_utc, data_origin, is_synthetic)"
                    " VALUES ('fk', 'AST-NOPE', 'M-1', 'main', ?, ?, 'manual', 0)", (BED, WAKE))
        sid = self.wearable()
        with closing(get_connection(self.db)) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sleep_stages VALUES (?, ?, ?, 'deep', 'wearable', 0, 0)",
                (sid, BED, WAKE))   # is_estimated = 0 is not allowed

    def test_repeated_initialization_is_safe(self):
        session_id = self.wearable()
        before = self.query("SELECT type, name, sql FROM sqlite_master ORDER BY name")
        for _ in range(3):
            quiet(initialize_database, self.db)
        self.assertEqual(self.query("SELECT type, name, sql FROM sqlite_master ORDER BY name"),
                         before)
        self.assertEqual(self.query("SELECT sleep_session_id FROM sleep_sessions"),
                         [(session_id,)])
        self.assertEqual(self.count("astronauts"), 2)


class BackwardCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "legacy.db"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript(LEGACY_DDL)
            conn.execute("INSERT INTO astronauts VALUES ('AST-001', 'Legacy Name')")
            conn.execute("INSERT INTO missions VALUES ('MISSION-DEMO-01', NULL)")
            conn.execute(
                "INSERT INTO measurements VALUES ('M-00001', 'AST-001', 'MISSION-DEMO-01',"
                " '2026-10-01T08:00:00Z', 'heart_rate', 72.0, 'bpm', 'synthetic', 1, 'ctx')")

    def snapshot(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return {
                "rows": [conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                         for t in sorted(LEGACY_TABLES)],
                "legacy_sql": conn.execute(
                    "SELECT name, sql FROM sqlite_master WHERE tbl_name IN "
                    "('astronauts', 'missions', 'measurements') ORDER BY name").fetchall(),
            }

    def test_upgrade_preserves_legacy_schema_and_rows(self):
        before = self.snapshot()
        quiet(initialize_database, self.db)
        quiet(initialize_database, self.db)
        self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.db)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(NEW_TABLES <= tables)

    def test_legacy_services_work_on_upgraded_database(self):
        quiet(initialize_database, self.db)
        rows = services.get_measurements("AST-001", db_path=self.db)
        self.assertEqual([r["measurement_id"] for r in rows], ["M-00001"])
        self.assertEqual(services.get_astronauts(db_path=self.db),
                         [{"astronaut_id": "AST-001", "display_name": "Legacy Name"}])
        self.assertEqual(services.get_missions("AST-001", db_path=self.db),
                         [{"mission_id": "MISSION-DEMO-01", "mission_name": None}])

    def test_new_records_link_to_legacy_astronaut(self):
        quiet(initialize_database, self.db)
        sa.add_sleep_session("AST-001", "MISSION-DEMO-01", BED, WAKE,
                             data_origin="manual", db_path=self.db)
        self.assertEqual(len(sa.get_sleep_sessions("AST-001", db_path=self.db)), 1)
        self.assertEqual(len(services.get_measurements("AST-001", db_path=self.db)), 1)


# ---------------------------------------------------------------------------
# Timestamps and intervals
# ---------------------------------------------------------------------------

class TimestampTests(TempDatabaseTestCase):
    def test_overnight_session_crossing_midnight(self):
        sid = self.wearable()
        [session] = sa.get_sleep_sessions("AST-A", db_path=self.db)
        self.assertEqual(session["sleep_session_id"], sid)
        self.assertEqual(session["bedtime_utc"], BED)
        self.assertEqual(session["wake_time_utc"], WAKE)
        self.assertEqual(session["time_in_bed_minutes"], 450.0)
        # Found by windows covering either calendar day.
        for start, end in (("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"),
                           ("2026-10-02T00:00:00Z", "2026-10-03T00:00:00Z")):
            with self.subTest(start=start):
                self.assertEqual(len(sa.get_sleep_sessions(
                    "AST-A", start_utc=start, end_utc=end, db_path=self.db)), 1)
        self.assertEqual(sa.get_sleep_sessions(
            "AST-A", start_utc=WAKE, end_utc="2026-10-03T00:00:00Z", db_path=self.db), [])

    def test_offsets_are_normalized_to_utc(self):
        self.manual(bedtime_utc="2026-10-02T00:30:00+02:00",
                    wake_time_utc=datetime(2026, 10, 2, 3, 0, tzinfo=timezone(timedelta(hours=-4))))
        [session] = sa.get_sleep_sessions("AST-A", db_path=self.db)
        self.assertEqual(session["bedtime_utc"], "2026-10-01T22:30:00Z")
        self.assertEqual(session["wake_time_utc"], "2026-10-02T07:00:00Z")

    def test_invalid_timestamps_rejected(self):
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        cases = {
            "naive": ("2026-10-01T22:00:00", WAKE),
            "malformed": ("yesterday", WAKE),
            "sub-second": ("2026-10-01T22:00:00.250Z", WAKE),
            "future wake": (BED, future),
            "not a string": (12345, WAKE),
        }
        for name, (bed, wake) in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                self.manual(bedtime_utc=bed, wake_time_utc=wake)
        self.assertEqual(self.count("sleep_sessions"), 0)

    def test_invalid_intervals_rejected(self):
        cases = {
            "zero length": (BED, BED),
            "reversed": (WAKE, BED),
            "over 24 hours": ("2026-10-01T06:00:00Z", "2026-10-02T06:01:00Z"),
        }
        for name, (bed, wake) in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                self.manual(bedtime_utc=bed, wake_time_utc=wake)
        with self.assertRaises(ValueError):
            sa.get_sleep_sessions("AST-A", start_utc=WAKE, end_utc=BED, db_path=self.db)
        self.assertEqual(self.count("sleep_sessions"), 0)


# ---------------------------------------------------------------------------
# Manual vs wearable sleep sessions, provenance, conflicts, duplicates
# ---------------------------------------------------------------------------

class SleepSessionTests(TempDatabaseTestCase):
    def test_manual_and_wearable_for_same_night_coexist(self):
        manual_id = self.manual(estimated_sleep_minutes=420, self_reported_quality=4)
        wearable_id = self.wearable()
        sessions = sa.get_sleep_sessions("AST-A", db_path=self.db)
        self.assertEqual({s["data_origin"] for s in sessions}, {"manual", "wearable"})
        [manual] = sa.get_sleep_sessions("AST-A", data_origin="manual", db_path=self.db)
        self.assertEqual(manual["sleep_session_id"], manual_id)
        self.assertIsNone(manual["sensor_quality"])
        self.assertIsNone(manual["sensor_coverage_fraction"])
        self.assertEqual(manual["self_reported_quality"], 4)
        self.assertIs(manual["is_synthetic"], False)
        [wear] = sa.get_sleep_sessions("AST-A", data_origin="wearable", db_path=self.db)
        self.assertEqual(wear["sleep_session_id"], wearable_id)
        self.assertEqual(wear["sensor_quality"], "good")

    def test_time_in_bed_is_not_treated_as_sleep(self):
        self.manual()   # bed/wake only
        [session] = sa.get_sleep_sessions("AST-A", db_path=self.db)
        self.assertIsNone(session["estimated_sleep_minutes"])
        self.assertEqual(session["time_in_bed_minutes"], 480.0)
        with self.assertRaisesRegex(ValueError, "exceed time in bed"):
            self.wearable(estimated_sleep_minutes=451)

    def test_provenance_rules(self):
        cases = {
            "manual with sensor quality": lambda: self.manual(sensor_quality="good"),
            "manual with coverage": lambda: self.manual(sensor_coverage_fraction=0.9),
            "wearable without quality": lambda: self.wearable(sensor_quality=None),
            "synthetic origin unflagged": lambda: self.manual(data_origin="synthetic"),
            "unknown origin": lambda: self.manual(data_origin="device"),
            "non-bool synthetic flag": lambda: self.manual(is_synthetic=1),
            "unknown sleep type": lambda: self.manual(sleep_type="siesta"),
            "quality out of range": lambda: self.manual(self_reported_quality=6),
            "coverage out of range": lambda: self.wearable(sensor_coverage_fraction=1.2),
            "unknown sensor quality": lambda: self.wearable(sensor_quality="great"),
            "negative estimate": lambda: self.wearable(estimated_sleep_minutes=-1),
            "nan estimate": lambda: self.wearable(estimated_sleep_minutes=float("nan")),
        }
        for name, action in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                action()
        self.assertEqual(self.count("sleep_sessions"), 0)

    def test_synthetic_wearable_is_labeled(self):
        self.wearable(is_synthetic=True)
        [session] = sa.get_sleep_sessions("AST-A", db_path=self.db)
        self.assertEqual(session["data_origin"], "wearable")
        self.assertIs(session["is_synthetic"], True)

    def test_missing_and_conflicting_sensor_observations(self):
        # Missing data is stored explicitly, not imputed.
        self.wearable(sensor_quality="unusable", sensor_coverage_fraction=0.05,
                      estimated_sleep_minutes=None, missing_data_notes="Signal lost")
        [session] = sa.get_sleep_sessions("AST-A", db_path=self.db)
        self.assertIsNone(session["estimated_sleep_minutes"])
        self.assertEqual(session["missing_data_notes"], "Signal lost")
        for name, kwargs in {
            "unusable with estimate": dict(sensor_quality="unusable"),
            "zero coverage with estimate": dict(sensor_coverage_fraction=0.0),
        }.items():
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, "Conflicting"):
                self.wearable(astronaut_id="AST-B", **kwargs)

    def test_overlapping_sessions_of_same_origin_rejected(self):
        self.wearable()
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.wearable(bedtime_utc="2026-10-02T05:00:00Z",
                          wake_time_utc="2026-10-02T07:00:00Z", estimated_sleep_minutes=None,
                          sleep_type="nap")
        # Adjacent (touching) sessions are not overlapping.
        self.wearable(bedtime_utc=WAKE, wake_time_utc="2026-10-02T07:00:00Z",
                      estimated_sleep_minutes=None, sleep_type="nap")
        # Another astronaut may sleep at the same time.
        self.wearable(astronaut_id="AST-B")
        self.assertEqual(self.count("sleep_sessions"), 3)

    def test_duplicate_inserts_are_idempotent(self):
        first = self.wearable(sleep_session_id="SLP-1")
        self.assertEqual(self.wearable(sleep_session_id="SLP-1"), first)
        # Same natural key, no explicit ID, identical content -> existing ID.
        self.assertEqual(self.wearable(), first)
        self.assertEqual(self.count("sleep_sessions"), 1)

    def test_conflicting_duplicates_rejected_without_overwrite(self):
        self.wearable(sleep_session_id="SLP-1")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.wearable(sleep_session_id="SLP-1", estimated_sleep_minutes=300)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.wearable(sleep_session_id="SLP-2", estimated_sleep_minutes=300)
        self.assertEqual(self.query("SELECT sleep_session_id, estimated_sleep_minutes "
                                    "FROM sleep_sessions"), [("SLP-1", 410.0)])

    def test_unknown_references_rejected(self):
        for kwargs in (dict(astronaut_id="AST-NOPE"), dict(mission_id="M-NOPE"),
                       dict(astronaut_id=""), dict(mission_id=None)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.wearable(**kwargs)


# ---------------------------------------------------------------------------
# Optional sleep stages
# ---------------------------------------------------------------------------

STAGES = [
    {"stage_type": "awake", "start_utc": BED, "end_utc": "2026-10-01T23:00:00Z"},
    {"stage_type": "light", "start_utc": "2026-10-01T23:00:00Z", "end_utc": "2026-10-02T00:00:00Z"},
    {"stage_type": "deep", "start_utc": "2026-10-02T00:00:00Z", "end_utc": "2026-10-02T01:00:00Z"},
    {"stage_type": "rem", "start_utc": "2026-10-02T01:00:00Z", "end_utc": "2026-10-02T02:00:00Z"},
    {"stage_type": "unknown", "start_utc": "2026-10-02T02:00:00Z", "end_utc": WAKE},
]


class SleepStageTests(TempDatabaseTestCase):
    def test_stages_are_optional(self):
        self.wearable()
        [session] = sa.get_sleep_sessions("AST-A", include_stages=True, db_path=self.db)
        self.assertEqual(session["sleep_stages"], [])
        self.assertEqual(session["stage_data_status"], "unavailable")
        self.assertNotIn("sleep_stages", sa.get_sleep_sessions("AST-A", db_path=self.db)[0])

    def test_stages_stored_as_estimates(self):
        sid = self.wearable()
        self.assertEqual(sa.add_sleep_stages(sid, list(reversed(STAGES)), db_path=self.db), 5)
        stages = sa.get_sleep_stages(sid, db_path=self.db)
        self.assertEqual([s["stage_type"] for s in stages],
                         ["awake", "light", "deep", "rem", "unknown"])
        self.assertTrue(all(s["is_estimated"] is True for s in stages))
        self.assertTrue(all(s["source"] == "wearable" for s in stages))
        [session] = sa.get_sleep_sessions("AST-A", include_stages=True, db_path=self.db)
        self.assertEqual(session["stage_data_status"], "available")
        # Idempotent re-add.
        self.assertEqual(sa.add_sleep_stages(sid, STAGES, db_path=self.db), 0)
        self.assertEqual(self.count("sleep_stages"), 5)

    def test_invalid_stages_rejected_atomically(self):
        sid = self.wearable()
        cases = {
            "outside session": {"stage_type": "light", "start_utc": "2026-10-02T06:00:00Z",
                                "end_utc": "2026-10-02T06:30:00Z"},
            "reversed": {"stage_type": "light", "start_utc": "2026-10-02T03:00:00Z",
                         "end_utc": "2026-10-02T02:30:00Z"},
            "overlap": {"stage_type": "rem", "start_utc": "2026-10-01T23:30:00Z",
                        "end_utc": "2026-10-02T00:30:00Z"},
            "bad type": {"stage_type": "N3", "start_utc": "2026-10-02T03:00:00Z",
                         "end_utc": "2026-10-02T03:30:00Z"},
            "claims verified": {**STAGES[0], "start_utc": "2026-10-02T03:00:00Z",
                                "end_utc": "2026-10-02T03:30:00Z", "is_estimated": False},
            "wrong source": {"stage_type": "light", "start_utc": "2026-10-02T03:00:00Z",
                             "end_utc": "2026-10-02T03:30:00Z", "source": "synthetic"},
            "extra field": {"stage_type": "light", "start_utc": "2026-10-02T03:00:00Z",
                            "end_utc": "2026-10-02T03:30:00Z", "confidence": 0.9},
        }
        for name, bad in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                sa.add_sleep_stages(sid, STAGES[:2] + [bad], db_path=self.db)
            self.assertEqual(self.count("sleep_stages"), 0)

    def test_manual_logs_cannot_have_stages(self):
        sid = self.manual()
        with self.assertRaisesRegex(ValueError, "Manual"):
            sa.add_sleep_stages(sid, [], db_path=self.db)
        with self.assertRaisesRegex(ValueError, "Unknown sleep session"):
            sa.add_sleep_stages("SLP-NOPE", STAGES, db_path=self.db)


# ---------------------------------------------------------------------------
# Alertness assessments and reaction-time trials
# ---------------------------------------------------------------------------

TRIALS = [
    {"reaction_time_ms": 250.0, "interstimulus_interval_ms": 2000},
    {"reaction_time_ms": 300.0, "interstimulus_interval_ms": 4000},
    {"reaction_time_ms": 520.0},                       # slow response -> lapse
    {"reaction_time_ms": None},                        # no response -> lapse
    {"reaction_time_ms": 80.0},                        # too fast -> false start
    {"reaction_time_ms": None, "responded_before_stimulus": True},
]


class AlertnessTests(TempDatabaseTestCase):
    def test_trials_and_summary_stored(self):
        aid = self.assessment(TRIALS, self_reported_fatigue=3)
        [assessment] = sa.get_alertness_assessments("AST-A", include_trials=True, db_path=self.db)
        self.assertEqual(assessment["assessment_id"], aid)
        self.assertEqual([t["outcome"] for t in assessment["trials"]],
                         ["valid", "valid", "lapse", "lapse", "false_start", "false_start"])
        self.assertEqual([t["trial_index"] for t in assessment["trials"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual(assessment["trials"][3]["reaction_time_ms"], None)
        self.assertIs(assessment["trials"][5]["responded_before_stimulus"], True)
        expected = {"trial_count": 6, "response_count": 3, "lapse_count": 2,
                    "false_start_count": 2, "median_rt_ms": 300.0, "mean_rt_ms": 356.667,
                    "min_rt_ms": 250.0, "max_rt_ms": 520.0, "self_reported_fatigue": 3,
                    "lapse_threshold_ms": 500.0, "false_start_threshold_ms": 100.0,
                    "protocol_version": "test-v1", "completion_status": "completed",
                    "timing_method": "software_monotonic_clock", "is_synthetic": False}
        self.assertEqual({k: assessment[k] for k in expected}, expected)
        self.assertAlmostEqual(assessment["sd_rt_ms"], 143.643, places=3)
        self.assertEqual(sa.get_reaction_time_trials(aid, db_path=self.db), assessment["trials"])

    def test_custom_protocol_thresholds_change_labels(self):
        self.assessment(TRIALS, lapse_threshold_ms=280, false_start_threshold_ms=50)
        [trials] = [a["trials"] for a in sa.get_alertness_assessments(
            "AST-A", include_trials=True, db_path=self.db)]
        self.assertEqual([t["outcome"] for t in trials],
                         ["valid", "lapse", "lapse", "lapse", "valid", "false_start"])

    def test_no_responses_have_no_rt_statistics(self):
        self.assessment([{"reaction_time_ms": None}], completion_status="incomplete")
        [a] = sa.get_alertness_assessments("AST-A", db_path=self.db)
        self.assertEqual((a["response_count"], a["lapse_count"]), (0, 1))
        self.assertIsNone(a["median_rt_ms"])
        self.assertIsNone(a["sd_rt_ms"])

    def test_invalid_assessments_rejected(self):
        cases = {
            "no trials": dict(trials=[]),
            "early with rt": dict(trials=[{"reaction_time_ms": 20,
                                           "responded_before_stimulus": True}]),
            "negative rt": dict(trials=[{"reaction_time_ms": -5}]),
            "bool rt": dict(trials=[{"reaction_time_ms": True}]),
            "duplicate index": dict(trials=[{"trial_index": 1, "reaction_time_ms": 250},
                                            {"trial_index": 1, "reaction_time_ms": 260}]),
            "unknown field": dict(trials=[{"reaction_time_ms": 250, "rt": 250}]),
            "summary supplied": dict(trials=[{"reaction_time_ms": 250, "median_rt_ms": 1}]),
            "thresholds inverted": dict(trials=TRIALS, lapse_threshold_ms=90),
            "fatigue out of range": dict(trials=TRIALS, self_reported_fatigue=8),
            "blank protocol": dict(trials=TRIALS, protocol_version=" "),
            "unknown timing": dict(trials=TRIALS, timing_method="stopwatch"),
            "synthetic timing unflagged": dict(trials=TRIALS, timing_method="synthetic_generator"),
            "synthetic origin unflagged": dict(trials=TRIALS, data_origin="synthetic"),
            "unknown astronaut": dict(trials=TRIALS, astronaut_id="AST-NOPE"),
            "naive timestamp": dict(trials=TRIALS, assessment_utc="2026-10-02T07:30:00"),
        }
        for name, kwargs in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                trials = kwargs.pop("trials")
                self.assessment(trials, **kwargs)
        self.assertEqual(self.count("alertness_assessments"), 0)
        self.assertEqual(self.count("reaction_time_trials"), 0)

    def test_duplicate_assessments(self):
        aid = self.assessment(TRIALS, assessment_id="ALR-1")
        self.assertEqual(self.assessment(TRIALS, assessment_id="ALR-1"), aid)
        self.assertEqual(self.assessment(TRIALS), aid)    # natural key, identical
        changed = [dict(t) for t in TRIALS]
        changed[0]["interstimulus_interval_ms"] = 2500    # summary identical, trial differs
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.assessment(changed, assessment_id="ALR-1")
        with self.assertRaises(ValueError):
            self.assessment(TRIALS[:2], assessment_id="ALR-2")
        self.assertEqual(self.count("alertness_assessments"), 1)
        self.assertEqual(self.count("reaction_time_trials"), 6)
        self.assertEqual(self.query("SELECT interstimulus_interval_ms FROM reaction_time_trials "
                                    "WHERE trial_index = 1"), [(2000.0,)])


# ---------------------------------------------------------------------------
# Optional cortisol
# ---------------------------------------------------------------------------

class CortisolTests(TempDatabaseTestCase):
    def add(self, **overrides):
        kwargs = dict(value=12.5, unit="nmol/L", sampling_method="saliva", db_path=self.db)
        kwargs.update(overrides)
        astronaut = kwargs.pop("astronaut_id", "AST-A")
        mission = kwargs.pop("mission_id", "M-1")
        when = kwargs.pop("sample_utc", "2026-10-02T06:45:00Z")
        return sa.add_cortisol_measurement(astronaut, mission, when, **kwargs)

    def test_cortisol_is_optional(self):
        self.wearable()
        self.assessment(TRIALS)
        self.assertEqual(sa.get_cortisol_measurements("AST-A", db_path=self.db), [])

    def test_unlinked_and_linked_samples(self):
        self.add(cortisol_id="CRT-1")
        sid = self.wearable()
        self.add(cortisol_id="CRT-2", sample_utc="2026-10-02T06:45:00Z", sampling_method="other",
                 sleep_session_id=sid)
        rows = sa.get_cortisol_measurements("AST-A", db_path=self.db)
        self.assertEqual([(r["cortisol_id"], r["minutes_since_awakening"], r["unit"])
                          for r in rows], [("CRT-1", None, "nmol/L"), ("CRT-2", 30.0, "nmol/L")])
        self.add(cortisol_id="CRT-3", sample_utc="2026-10-02T07:15:00Z",
                 sleep_session_id=sid, minutes_since_awakening=60, unit="ug/dL",
                 value=0.45, data_origin="synthetic", is_synthetic=True)
        row = sa.get_cortisol_measurements("AST-A", start_utc="2026-10-02T07:00:00Z",
                                           db_path=self.db)[0]
        self.assertEqual((row["unit"], row["value"], row["is_synthetic"]), ("ug/dL", 0.45, True))

    def test_invalid_cortisol_rejected(self):
        sid = self.wearable()
        cases = {
            "implicit unit": dict(unit="nmol"),
            "zero value": dict(value=0),
            "missing value": dict(value=None),
            "unknown method": dict(sampling_method="urine"),
            "synthetic unflagged": dict(data_origin="synthetic"),
            "negative minutes": dict(minutes_since_awakening=-3),
            "before wake": dict(sleep_session_id=sid, sample_utc="2026-10-02T05:00:00Z"),
            "minutes conflict": dict(sleep_session_id=sid, minutes_since_awakening=90),
            "unknown session": dict(sleep_session_id="SLP-NOPE"),
            "other astronaut's session": dict(astronaut_id="AST-B", sleep_session_id=sid),
            "other mission's session": dict(mission_id="M-2", sleep_session_id=sid),
        }
        for name, kwargs in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                self.add(**kwargs)
        self.assertEqual(self.count("cortisol_measurements"), 0)

    def test_duplicate_cortisol(self):
        first = self.add(cortisol_id="CRT-1")
        self.assertEqual(self.add(cortisol_id="CRT-1"), first)
        self.assertEqual(self.add(), first)
        with self.assertRaises(ValueError):
            self.add(cortisol_id="CRT-1", value=99)
        self.assertEqual(self.count("cortisol_measurements"), 1)


# ---------------------------------------------------------------------------
# Isolation between astronauts and missions
# ---------------------------------------------------------------------------

class IsolationTests(TempDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.ids = {}
        nights = {("AST-A", "M-1"): "2026-10-01", ("AST-A", "M-2"): "2026-10-05",
                  ("AST-B", "M-1"): "2026-10-01", ("AST-B", "M-2"): "2026-10-05"}
        for (astronaut, mission), day in nights.items():
            bed = f"{day}T22:00:00Z"
            wake = (datetime.fromisoformat(bed) + timedelta(hours=8)).isoformat()
            sid = sa.add_sleep_session(astronaut, mission, bed, wake, data_origin="manual",
                                       db_path=self.db)
            aid = self.assessment(TRIALS, astronaut_id=astronaut, mission_id=mission,
                                  assessment_utc=wake)
            cid = sa.add_cortisol_measurement(astronaut, mission, wake, 10.0, "nmol/L", "saliva",
                                              sleep_session_id=sid, db_path=self.db)
            self.ids[astronaut, mission] = (sid, aid, cid)

    def test_retrieval_scoped_to_astronaut_and_mission(self):
        for (astronaut, mission), (sid, aid, cid) in self.ids.items():
            with self.subTest(astronaut=astronaut, mission=mission):
                self.assertEqual([s["sleep_session_id"] for s in sa.get_sleep_sessions(
                    astronaut, mission_id=mission, db_path=self.db)], [sid])
                self.assertEqual([a["assessment_id"] for a in sa.get_alertness_assessments(
                    astronaut, mission_id=mission, db_path=self.db)], [aid])
                self.assertEqual([c["cortisol_id"] for c in sa.get_cortisol_measurements(
                    astronaut, mission_id=mission, db_path=self.db)], [cid])
        all_a = sa.get_sleep_sessions("AST-A", db_path=self.db)
        self.assertEqual({s["astronaut_id"] for s in all_a}, {"AST-A"})
        self.assertEqual(len(all_a), 2)

    def test_unknown_filters_return_empty_and_blank_ids_raise(self):
        self.assertEqual(sa.get_sleep_sessions("AST-NOPE", db_path=self.db), [])
        self.assertEqual(sa.get_alertness_assessments("AST-A", mission_id="M-NOPE",
                                                      db_path=self.db), [])
        for func in (sa.get_sleep_sessions, sa.get_alertness_assessments,
                     sa.get_cortisol_measurements):
            with self.subTest(func=func.__name__), self.assertRaises(ValueError):
                func(" ", db_path=self.db)

    def test_new_records_do_not_affect_legacy_measurements(self):
        self.assertEqual(services.get_measurements("AST-A", db_path=self.db), [])
        self.assertEqual(services.get_missions("AST-A", db_path=self.db), [])


# ---------------------------------------------------------------------------
# Dataset import
# ---------------------------------------------------------------------------

class ImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.data_dir = self.temp / "dataset"
        self.expected = write_dataset(self.data_dir)
        self.db = self.temp / "import.db"

    def run_import(self):
        return quiet(import_sleep_alertness, self.data_dir, db_path=self.db)

    def counts(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}

    def edit_csv(self, name, line_index, column, value):
        path = self.data_dir / f"{name}.csv"
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        rows[line_index][column] = value
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_import_is_idempotent(self):
        first = self.run_import()
        self.assertEqual({t: c["inserted"] for t, c in first.items()}, self.expected)
        self.assertEqual(self.counts(), self.expected)
        second = self.run_import()
        self.assertEqual({t: c["inserted"] for t, c in second.items()},
                         {t: 0 for t in TABLES})
        self.assertEqual({t: c["skipped"] for t, c in second.items()}, self.expected)
        self.assertEqual(self.counts(), self.expected)

    def test_conflicting_reimport_rolls_back_and_never_overwrites(self):
        self.run_import()
        with closing(sqlite3.connect(self.db)) as conn:
            before = conn.execute("SELECT * FROM sleep_sessions ORDER BY 1").fetchall()
        self.edit_csv("sleep_sessions", 0, "notes", "tampered")
        with self.assertRaisesRegex(ValueError, "sleep_sessions.csv line 2"):
            self.run_import()
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT * FROM sleep_sessions ORDER BY 1").fetchall(),
                             before)
        self.assertEqual(self.counts(), self.expected)

    def test_failed_import_leaves_no_partial_data(self):
        self.edit_csv("cortisol_measurements", -1, "unit", "mg")
        with self.assertRaisesRegex(ValueError, "unit"):
            self.run_import()
        self.assertEqual(self.counts(), {t: 0 for t in TABLES})

    def test_non_synthetic_rows_rejected(self):
        self.edit_csv("alertness_assessments", 0, "is_synthetic", "False")
        with self.assertRaisesRegex(ValueError, "expected synthetic"):
            self.run_import()
        self.assertEqual(self.counts(), {t: 0 for t in TABLES})

    def test_existing_astronaut_rows_not_overwritten(self):
        quiet(initialize_database, self.db)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO astronauts VALUES ('AST-S01', 'Existing Name')")
        self.run_import()
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT display_name FROM astronauts WHERE "
                                          "astronaut_id = 'AST-S01'").fetchone(),
                             ("Existing Name",))

    def test_missing_files_reported(self):
        (self.data_dir / "sleep_stages.csv").unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_import()


if __name__ == "__main__":
    unittest.main()
