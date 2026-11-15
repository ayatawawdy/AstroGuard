
from pathlib import Path
import sys

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


def validate_data(data_file=None):
    """Validate the dataset and return a list of error messages (empty if valid)."""
    df = pd.read_csv(data_file if data_file is not None else DATA_FILE)

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
    if "astronaut_id" in df.columns:
        print(f"Astronauts: {df['astronaut_id'].nunique()}")

    if errors:
        print("\nVALIDATION FAILED")
        for error in errors:
            print(f"- {error}")
    else:
        print("\nVALIDATION PASSED")
        print("All basic schema checks passed!")

    return errors


def main(argv=None):
    """Command-line entry point. Returns 0 when valid and 1 when validation fails."""
    args = sys.argv[1:] if argv is None else argv
    errors = validate_data(Path(args[0]) if args else None)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
