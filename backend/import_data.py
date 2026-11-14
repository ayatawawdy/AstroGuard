
import csv
import math
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from backend.database import get_connection, initialize_database


BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "data" / "processed" / "health_measurements_clean.csv"

METRIC_UNITS = {
    "heart_rate": "bpm",
    "systolic_bp": "mmHg",
    "diastolic_bp": "mmHg",
    "body_temperature": "°C",
    "blood_glucose": "mg/dL",
    "sleep_duration": "hours",
    "mood": "1-5",
    "stress_level": "1-10",
}

COLUMNS = [
    "measurement_id",
    "astronaut_id",
    "mission_id",
    "timestamp_utc",
    "metric_type",
    "value",
    "unit",
    "source",
    "is_synthetic",
    "context",
]


def import_data(csv_path=CSV_PATH):
    csv_path = Path(csv_path)

    if not csv_path.exists():
        raise FileNotFoundError(f"Dataset not found: {csv_path}")

    records = []
    seen_ids = set()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        if not set(COLUMNS).issubset(reader.fieldnames or []):
            raise ValueError("CSV is missing required columns")

        for row in reader:
            measurement_id = row["measurement_id"]
            astronaut_id = row["astronaut_id"]
            mission_id = row["mission_id"]
            metric = row["metric_type"]

            if not all([measurement_id, astronaut_id, mission_id]):
                raise ValueError("Missing required identifier")

            if measurement_id in seen_ids:
                raise ValueError(f"Duplicate ID in CSV: {measurement_id}")
            seen_ids.add(measurement_id)

            if metric not in METRIC_UNITS:
                raise ValueError(f"Unknown metric: {metric}")

            if row["unit"] != METRIC_UNITS[metric]:
                raise ValueError(f"Invalid unit for {metric}")

            if (row["source"] != "synthetic" or
                    row["is_synthetic"].lower() != "true"):
                raise ValueError("Expected synthetic demo data")

            value = float(row["value"])
            if not math.isfinite(value):
                raise ValueError("Non-finite measurement value")

            timestamp = datetime.fromisoformat(
                row["timestamp_utc"].replace("Z", "+00:00")
            )
            if timestamp.tzinfo is None:
                raise ValueError("Timestamp must include timezone")
            if timestamp.astimezone(timezone.utc) > datetime.now(timezone.utc):
                raise ValueError("Future measurement timestamp")

            records.append((
                measurement_id,
                astronaut_id,
                mission_id,
                row["timestamp_utc"],
                metric,
                value,
                row["unit"],
                "synthetic",
                1,
                row["context"] or None,
            ))

    initialize_database()

    inserted = 0
    skipped = 0

    with closing(get_connection()) as conn:
        with conn:
            for record in records:
                conn.execute(
                    "INSERT OR IGNORE INTO astronauts (astronaut_id) VALUES (?)",
                    (record[1],),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO missions (mission_id) VALUES (?)",
                    (record[2],),
                )

                existing = conn.execute(
                    "SELECT * FROM measurements WHERE measurement_id = ?",
                    (record[0],),
                ).fetchone()

                if existing is not None:
                    if tuple(existing) != record:
                        raise ValueError(
                            f"Conflicting measurement ID: {record[0]}"
                        )
                    skipped += 1
                    continue

                conn.execute(
                    """
                    INSERT INTO measurements (
                        measurement_id, astronaut_id, mission_id,
                        timestamp_utc, metric_type, value, unit,
                        source, is_synthetic, context
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    record,
                )
                inserted += 1

    print("DATA IMPORT COMPLETED")
    print(f"Records in CSV: {len(records)}")
    print(f"New measurements: {inserted}")
    print(f"Already imported: {skipped}")


if __name__ == "__main__":
    import_data()
