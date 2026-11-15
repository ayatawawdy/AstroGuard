
import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# Optional override (e.g. a fresh demo database): set ASTROGUARD_DB_PATH before
# starting Python. Without it the original backend/astroguard.db is used.
DB_PATH = Path(os.environ.get("ASTROGUARD_DB_PATH") or BASE_DIR / "astroguard.db")


def get_connection(db_path=DB_PATH):
    """Create a connection to the SQLite database."""

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row

    # Enforce foreign key relationships
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


# Stage 1A sleep and alertness tables (see docs/contracts.md).
#
# Additive and backward compatible: every statement is CREATE ... IF NOT EXISTS,
# no legacy table is altered, and nothing is dropped or overwritten. Timestamps
# are stored as normalized UTC text "YYYY-MM-DDTHH:MM:SSZ"; julianday() checks
# reject malformed text (julianday returns NULL for it) and reversed intervals.
# These are technical data-integrity rules, not clinical thresholds.
SLEEP_ALERTNESS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS sleep_sessions (
        sleep_session_id TEXT PRIMARY KEY,
        astronaut_id TEXT NOT NULL,
        mission_id TEXT NOT NULL,
        sleep_type TEXT NOT NULL
            CHECK (sleep_type IN ('main', 'nap')),
        bedtime_utc TEXT NOT NULL
            CHECK (julianday(bedtime_utc) IS NOT NULL),
        wake_time_utc TEXT NOT NULL
            CHECK (julianday(wake_time_utc) IS NOT NULL),
        estimated_sleep_minutes REAL
            CHECK (estimated_sleep_minutes IS NULL
                   OR estimated_sleep_minutes >= 0),
        data_origin TEXT NOT NULL
            CHECK (data_origin IN ('manual', 'wearable', 'synthetic')),
        is_synthetic INTEGER NOT NULL
            CHECK (is_synthetic IN (0, 1)),
        self_reported_quality INTEGER
            CHECK (self_reported_quality IS NULL
                   OR self_reported_quality BETWEEN 1 AND 5),
        sensor_quality TEXT
            CHECK (sensor_quality IS NULL OR sensor_quality IN (
                'good', 'fair', 'poor', 'unusable'
            )),
        sensor_coverage_fraction REAL
            CHECK (sensor_coverage_fraction IS NULL
                   OR sensor_coverage_fraction BETWEEN 0 AND 1),
        missing_data_notes TEXT,
        notes TEXT,

        CHECK (julianday(wake_time_utc) > julianday(bedtime_utc)),
        CHECK (data_origin != 'synthetic' OR is_synthetic = 1),
        CHECK (data_origin != 'manual'
               OR (sensor_quality IS NULL
                   AND sensor_coverage_fraction IS NULL)),
        CHECK (data_origin != 'wearable' OR sensor_quality IS NOT NULL),

        UNIQUE (astronaut_id, data_origin, bedtime_utc),

        FOREIGN KEY (astronaut_id) REFERENCES astronauts(astronaut_id),
        FOREIGN KEY (mission_id) REFERENCES missions(mission_id)
    );

    CREATE INDEX IF NOT EXISTS idx_sleep_sessions_astronaut_mission_time
    ON sleep_sessions (astronaut_id, mission_id, bedtime_utc);

    CREATE TABLE IF NOT EXISTS sleep_stages (
        sleep_session_id TEXT NOT NULL,
        start_utc TEXT NOT NULL
            CHECK (julianday(start_utc) IS NOT NULL),
        end_utc TEXT NOT NULL
            CHECK (julianday(end_utc) IS NOT NULL),
        stage_type TEXT NOT NULL
            CHECK (stage_type IN ('awake', 'light', 'deep', 'rem', 'unknown')),
        source TEXT NOT NULL
            CHECK (source IN ('wearable', 'synthetic')),
        is_estimated INTEGER NOT NULL DEFAULT 1
            CHECK (is_estimated = 1),
        is_synthetic INTEGER NOT NULL
            CHECK (is_synthetic IN (0, 1)),

        CHECK (julianday(end_utc) > julianday(start_utc)),
        CHECK (source != 'synthetic' OR is_synthetic = 1),

        PRIMARY KEY (sleep_session_id, start_utc),
        FOREIGN KEY (sleep_session_id)
            REFERENCES sleep_sessions(sleep_session_id)
    );

    CREATE TABLE IF NOT EXISTS alertness_assessments (
        assessment_id TEXT PRIMARY KEY,
        astronaut_id TEXT NOT NULL,
        mission_id TEXT NOT NULL,
        assessment_utc TEXT NOT NULL
            CHECK (julianday(assessment_utc) IS NOT NULL),
        protocol_version TEXT NOT NULL
            CHECK (length(trim(protocol_version)) > 0),
        timing_method TEXT NOT NULL
            CHECK (timing_method IN (
                'software_monotonic_clock', 'browser_event_timestamp',
                'synthetic_generator', 'unknown'
            )),
        completion_status TEXT NOT NULL
            CHECK (completion_status IN ('completed', 'incomplete')),
        data_origin TEXT NOT NULL
            CHECK (data_origin IN ('app', 'synthetic')),
        is_synthetic INTEGER NOT NULL
            CHECK (is_synthetic IN (0, 1)),
        lapse_threshold_ms REAL NOT NULL
            CHECK (lapse_threshold_ms > 0),
        false_start_threshold_ms REAL NOT NULL
            CHECK (false_start_threshold_ms >= 0),
        trial_count INTEGER NOT NULL
            CHECK (trial_count >= 1),
        response_count INTEGER NOT NULL
            CHECK (response_count >= 0),
        lapse_count INTEGER NOT NULL
            CHECK (lapse_count >= 0),
        false_start_count INTEGER NOT NULL
            CHECK (false_start_count >= 0),
        mean_rt_ms REAL,
        median_rt_ms REAL,
        sd_rt_ms REAL,
        min_rt_ms REAL,
        max_rt_ms REAL,
        self_reported_fatigue INTEGER
            CHECK (self_reported_fatigue IS NULL
                   OR self_reported_fatigue BETWEEN 1 AND 7),
        notes TEXT,

        CHECK (data_origin != 'synthetic' OR is_synthetic = 1),
        CHECK (false_start_threshold_ms < lapse_threshold_ms),

        UNIQUE (astronaut_id, assessment_utc),

        FOREIGN KEY (astronaut_id) REFERENCES astronauts(astronaut_id),
        FOREIGN KEY (mission_id) REFERENCES missions(mission_id)
    );

    CREATE INDEX IF NOT EXISTS idx_alertness_astronaut_mission_time
    ON alertness_assessments (astronaut_id, mission_id, assessment_utc);

    CREATE TABLE IF NOT EXISTS reaction_time_trials (
        assessment_id TEXT NOT NULL,
        trial_index INTEGER NOT NULL
            CHECK (trial_index >= 1),
        interstimulus_interval_ms REAL
            CHECK (interstimulus_interval_ms IS NULL
                   OR interstimulus_interval_ms > 0),
        reaction_time_ms REAL
            CHECK (reaction_time_ms IS NULL OR reaction_time_ms >= 0),
        responded_before_stimulus INTEGER NOT NULL DEFAULT 0
            CHECK (responded_before_stimulus IN (0, 1)),
        outcome TEXT NOT NULL
            CHECK (outcome IN ('valid', 'lapse', 'false_start')),

        CHECK (responded_before_stimulus = 0 OR reaction_time_ms IS NULL),

        PRIMARY KEY (assessment_id, trial_index),
        FOREIGN KEY (assessment_id)
            REFERENCES alertness_assessments(assessment_id)
    );

    CREATE TABLE IF NOT EXISTS cortisol_measurements (
        cortisol_id TEXT PRIMARY KEY,
        astronaut_id TEXT NOT NULL,
        mission_id TEXT NOT NULL,
        sample_utc TEXT NOT NULL
            CHECK (julianday(sample_utc) IS NOT NULL),
        value REAL NOT NULL
            CHECK (value > 0),
        unit TEXT NOT NULL
            CHECK (unit IN ('nmol/L', 'ug/dL')),
        sampling_method TEXT NOT NULL
            CHECK (sampling_method IN (
                'saliva', 'blood_serum', 'blood_plasma', 'other'
            )),
        minutes_since_awakening REAL
            CHECK (minutes_since_awakening IS NULL
                   OR minutes_since_awakening >= 0),
        sleep_session_id TEXT,
        data_origin TEXT NOT NULL
            CHECK (data_origin IN ('manual', 'synthetic')),
        is_synthetic INTEGER NOT NULL
            CHECK (is_synthetic IN (0, 1)),
        notes TEXT,

        CHECK (data_origin != 'synthetic' OR is_synthetic = 1),

        UNIQUE (astronaut_id, sample_utc, sampling_method),

        FOREIGN KEY (astronaut_id) REFERENCES astronauts(astronaut_id),
        FOREIGN KEY (mission_id) REFERENCES missions(mission_id),
        FOREIGN KEY (sleep_session_id)
            REFERENCES sleep_sessions(sleep_session_id)
    );

    CREATE INDEX IF NOT EXISTS idx_cortisol_astronaut_mission_time
    ON cortisol_measurements (astronaut_id, mission_id, sample_utc);
"""


# Decision-support tables (see docs/contracts.md sections 7 and 9). Additive only.
DECISION_SUPPORT_SCHEMA = """
    CREATE TABLE IF NOT EXISTS rest_notification_events (
        event_id TEXT PRIMARY KEY,
        astronaut_id TEXT NOT NULL,
        mission_id TEXT NOT NULL,
        created_utc TEXT NOT NULL
            CHECK (julianday(created_utc) IS NOT NULL),
        evaluated_at_utc TEXT NOT NULL
            CHECK (julianday(evaluated_at_utc) IS NOT NULL),
        alertness_status TEXT NOT NULL
            CHECK (alertness_status IN (
                'normal', 'attention_needed', 'rest_recommended', 'insufficient_data'
            )),
        severity TEXT NOT NULL
            CHECK (severity IN ('info', 'advisory', 'warning')),
        title TEXT NOT NULL,
        explanation TEXT NOT NULL,
        suggested_action TEXT NOT NULL,
        task_context TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        evidence_key TEXT NOT NULL,
        source_assessment_id TEXT,
        analysis_version TEXT NOT NULL,
        is_synthetic_input INTEGER NOT NULL
            CHECK (is_synthetic_input IN (0, 1)),
        acknowledged INTEGER NOT NULL DEFAULT 0
            CHECK (acknowledged IN (0, 1)),
        acknowledged_utc TEXT,
        acknowledged_by TEXT,

        CHECK ((acknowledged = 0 AND acknowledged_utc IS NULL)
               OR (acknowledged = 1 AND julianday(acknowledged_utc) IS NOT NULL)),

        FOREIGN KEY (astronaut_id) REFERENCES astronauts(astronaut_id),
        FOREIGN KEY (mission_id) REFERENCES missions(mission_id)
    );

    CREATE INDEX IF NOT EXISTS idx_notifications_astronaut_mission_time
    ON rest_notification_events (astronaut_id, mission_id, created_utc);

    CREATE TABLE IF NOT EXISTS actigraphy_recordings (
        recording_id TEXT PRIMARY KEY,
        astronaut_id TEXT NOT NULL,
        mission_id TEXT NOT NULL,
        source_format TEXT NOT NULL
            CHECK (source_format IN ('actiware_csv', 'astroguard_synthetic')),
        source_name TEXT NOT NULL,
        source_subject_code TEXT,
        device_model TEXT,
        epoch_seconds INTEGER NOT NULL
            CHECK (epoch_seconds > 0),
        start_utc TEXT NOT NULL
            CHECK (julianday(start_utc) IS NOT NULL),
        end_utc TEXT NOT NULL
            CHECK (julianday(end_utc) IS NOT NULL),
        epoch_count INTEGER NOT NULL
            CHECK (epoch_count > 0),
        data_origin TEXT NOT NULL
            CHECK (data_origin IN ('actiwatch_export', 'synthetic')),
        is_synthetic INTEGER NOT NULL
            CHECK (is_synthetic IN (0, 1)),
        attribution TEXT,

        CHECK (julianday(end_utc) > julianday(start_utc)),
        CHECK (data_origin != 'synthetic' OR is_synthetic = 1),

        FOREIGN KEY (astronaut_id) REFERENCES astronauts(astronaut_id),
        FOREIGN KEY (mission_id) REFERENCES missions(mission_id)
    );

    CREATE TABLE IF NOT EXISTS actigraphy_epochs (
        recording_id TEXT NOT NULL,
        epoch_start_utc TEXT NOT NULL
            CHECK (julianday(epoch_start_utc) IS NOT NULL),
        activity_count REAL
            CHECK (activity_count IS NULL OR activity_count >= 0),
        white_light_lux REAL
            CHECK (white_light_lux IS NULL OR white_light_lux >= 0),
        off_wrist INTEGER
            CHECK (off_wrist IS NULL OR off_wrist IN (0, 1)),
        event_marker INTEGER
            CHECK (event_marker IS NULL OR event_marker IN (0, 1)),
        device_sleep_wake TEXT
            CHECK (device_sleep_wake IS NULL OR device_sleep_wake IN ('sleep', 'wake')),
        interval_status TEXT
            CHECK (interval_status IS NULL OR interval_status IN (
                'active', 'rest', 'rest_sleep', 'excluded'
            )),

        PRIMARY KEY (recording_id, epoch_start_utc),
        FOREIGN KEY (recording_id) REFERENCES actigraphy_recordings(recording_id)
    );
"""


def initialize_database(db_path=DB_PATH):
    """Create the required tables if they do not exist.

    Safe to call repeatedly: existing tables and rows are never modified.
    Creates the legacy astronauts/missions/measurements tables and the
    Stage 1A sleep and alertness tables (``SLEEP_ALERTNESS_SCHEMA``).
    """

    connection = get_connection(db_path)

    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS astronauts (
                astronaut_id TEXT PRIMARY KEY,
                display_name TEXT
            );

            CREATE TABLE IF NOT EXISTS missions (
                mission_id TEXT PRIMARY KEY,
                mission_name TEXT
            );

            CREATE TABLE IF NOT EXISTS measurements (
                measurement_id TEXT PRIMARY KEY,
                astronaut_id TEXT NOT NULL,
                mission_id TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                source TEXT NOT NULL
                    CHECK (source IN (
                        'manual', 'device', 'synthetic'
                    )),
                is_synthetic INTEGER NOT NULL
                    CHECK (is_synthetic IN (0, 1)),
                context TEXT,

                CHECK (
                    (source = 'synthetic' AND is_synthetic = 1)
                    OR
                    (source != 'synthetic' AND is_synthetic = 0)
                ),

                FOREIGN KEY (astronaut_id)
                    REFERENCES astronauts(astronaut_id),

                FOREIGN KEY (mission_id)
                    REFERENCES missions(mission_id)
            );

            CREATE INDEX IF NOT EXISTS idx_measurements_astronaut_time
            ON measurements (astronaut_id, timestamp_utc);

            CREATE INDEX IF NOT EXISTS idx_measurements_metric
            ON measurements (metric_type);
        """)
        connection.executescript(SLEEP_ALERTNESS_SCHEMA)
        connection.executescript(DECISION_SUPPORT_SCHEMA)

        connection.commit()
        print("Database initialized successfully!")

    finally:
        connection.close()


if __name__ == "__main__":
    initialize_database()
