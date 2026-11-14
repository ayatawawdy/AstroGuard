
from pathlib import Path
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "synthetic" / "health_measurements.csv"
OUTPUT_DIR = BASE_DIR / "processed"

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

TEXT_COLUMNS = [
    "measurement_id",
    "astronaut_id",
    "mission_id",
    "metric_type",
    "unit",
    "source",
]

REQUIRED_COLUMNS = TEXT_COLUMNS + [
    "timestamp_utc",
    "value",
    "is_synthetic",
]



def clean_data(
    input_file: Path | None = None,
    output_dir: Path | None = None
):
    input_path = input_file if input_file is not None else INPUT_FILE
    output_path = output_dir if output_dir is not None else OUTPUT_DIR

    df = pd.read_csv(input_path)


    missing_columns = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing_columns:
        raise ValueError(f"Missing columns: {missing_columns}")

    # Normalize text fields
    for column in TEXT_COLUMNS:
        df[column] = df[column].astype("string").str.strip()

    reasons = pd.Series("", index=df.index, dtype="string")

    def flag(condition, message):
        mask = pd.Series(condition, index=df.index).fillna(True)
        reasons.loc[mask] += message + "; "

    # Check required values
    for column in REQUIRED_COLUMNS:
        missing = df[column].isna()
        if column in TEXT_COLUMNS:
            missing = missing | df[column].eq("").fillna(True)

        flag(missing, f"Missing {column}")

    # Check duplicates
    flag(
        df["measurement_id"].duplicated(keep="first"),
        "Duplicate measurement ID",
    )

    # Check metric types and units
    flag(
        ~df["metric_type"].isin(METRIC_UNITS),
        "Unknown metric type",
    )

    expected_units = df["metric_type"].map(METRIC_UNITS)
    flag(
        df["unit"] != expected_units,
        "Invalid unit",
    )

    # Validate numerical values
    numeric_values = pd.to_numeric(df["value"], errors="coerce")
    flag(
        ~np.isfinite(numeric_values),
        "Invalid numeric value",
    )

    # Validate timestamps
    timestamps = pd.to_datetime(
        df["timestamp_utc"], errors="coerce", utc=True
    )
    flag(
        timestamps.isna()
        | (timestamps > pd.Timestamp.now(tz="UTC")),
        "Invalid or future timestamp",
    )

    # Validate synthetic data labeling
    synthetic_flags = (
        df["is_synthetic"]
        .astype("string")
        .str.lower()
        .eq("true")
        .fillna(False)
    )
    flag(~synthetic_flags, "Invalid synthetic flag")
    flag(
        ~df["source"].str.lower().eq("synthetic").fillna(False),
        "Invalid source",
    )

    # Separate valid and rejected records
    valid_mask = reasons.eq("")

    clean_df = df.loc[valid_mask].copy()
    clean_df["value"] = numeric_values.loc[valid_mask]
    clean_df["timestamp_utc"] = (
        timestamps.loc[valid_mask]
        .dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    clean_df["is_synthetic"] = True

    rejected_df = df.loc[~valid_mask].copy()
    rejected_df["rejection_reason"] = (
        reasons.loc[~valid_mask].str.rstrip("; ")
    )


    # Save outputs
    output_path.mkdir(parents=True, exist_ok=True)

    clean_df.to_csv(
        output_path / "health_measurements_clean.csv",
        index=False,
        encoding="utf-8",
    )

    rejected_df.to_csv(
        output_path / "rejected_records.csv",
        index=False,
        encoding="utf-8",
    )

    print("DATA CLEANING COMPLETED")
    print(f"Original records: {len(df)}")
    print(f"Clean records: {len(clean_df)}")
    print(f"Rejected records: {len(rejected_df)}")
    print(f"Output folder: {output_path}")

    return clean_df, rejected_df



if __name__ == "__main__":
    clean_data()
