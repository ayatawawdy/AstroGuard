
from pathlib import Path
import pandas as pd

# Locate the generated dataset
DATA_FILE = (
    Path(__file__).resolve().parent
    / "synthetic"
    / "health_measurements.csv"
)

EXPECTED_COLUMNS = [
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


def validate_data():
    df = pd.read_csv(DATA_FILE)

    print("Starting data validation...")
    errors = []

    # Check required columns
    missing_columns = set(EXPECTED_COLUMNS) - set(df.columns)
    if missing_columns:
        errors.append(f"Missing columns: {missing_columns}")
    else:
        # Required fields must not contain missing values
        required = [c for c in EXPECTED_COLUMNS if c != "context"]
        if df[required].isna().any().any():
            errors.append("Missing values in required fields")

        # Check measurement IDs
        if df["measurement_id"].duplicated().any():
            errors.append("Duplicate measurement IDs")

        # Check metric names
        invalid_metrics = ~df["metric_type"].isin(METRIC_UNITS)
        if invalid_metrics.any():
            errors.append("Unknown metric types")

        # Check measurement units
        expected_units = df["metric_type"].map(METRIC_UNITS)
        if (df["unit"] != expected_units).any():
            errors.append("Invalid measurement units")

        # Check numeric values
        values = pd.to_numeric(df["value"], errors="coerce")
        if values.isna().any() or not values.map(
            lambda x: pd.notna(x) and abs(x) != float("inf")
        ).all():
            errors.append("Invalid numeric values")

        # Check timestamps
        timestamps = pd.to_datetime(
            df["timestamp_utc"], errors="coerce", utc=True
        )
        if timestamps.isna().any():
            errors.append("Invalid timestamps")
        if (timestamps > pd.Timestamp.now(tz="UTC")).any():
            errors.append("Future timestamps")

        # Check synthetic data labeling
        if not df["source"].eq("synthetic").all():
            errors.append("Unexpected data source")
        if not df["is_synthetic"].astype(str).str.lower().eq("true").all():
            errors.append("Synthetic data not correctly labeled")

    print(f"Total records: {len(df)}")
    print(f"Astronauts: {df['astronaut_id'].nunique()}")

    if errors:
        print("\nVALIDATION FAILED")
        for error in errors:
            print(f"- {error}")
    else:
        print("\nVALIDATION PASSED")
        print("All basic schema checks passed!")


if __name__ == "__main__":
    validate_data()
