
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "astroguard.db"


def get_connection(db_path=DB_PATH):
    """Create a connection to the SQLite database."""

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row

    # Enforce foreign key relationships
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def initialize_database(db_path=DB_PATH):
    """Create the required tables if they do not exist."""

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

        connection.commit()
        print("Database initialized successfully!")

    finally:
        connection.close()


if __name__ == "__main__":
    initialize_database()
