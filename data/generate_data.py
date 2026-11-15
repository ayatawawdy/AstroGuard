
from pathlib import Path
from datetime import datetime, timedelta, timezone
import random
import pandas as pd

# Reproducible synthetic data
rng = random.Random(42)

ASTRONAUTS = ["AST-001", "AST-002", "AST-003"]
MISSION_ID = "MISSION-DEMO-01"
START_DATE = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
DAYS = 30

UNITS = {
    "heart_rate": "bpm",
    "systolic_bp": "mmHg",
    "diastolic_bp": "mmHg",
    "body_temperature": "°C",
    "blood_glucose": "mg/dL",
    "sleep_duration": "hours",
    "mood": "1-5",
    "stress_level": "1-10",
}

records: list[dict[str, str | float | bool]] = []

for astronaut_id in ASTRONAUTS:
    for day in range(DAYS):
        timestamp = (
            START_DATE + timedelta(days=day)
        ).isoformat().replace("+00:00", "Z")

        # Fictional demonstration measurements
        diastolic = rng.randint(65, 85)

        measurements = {
            "heart_rate": rng.randint(60, 90),
            "systolic_bp": rng.randint(105, 130),
            "diastolic_bp": diastolic,
            "body_temperature": round(rng.uniform(36.1, 37.2), 1),
            "blood_glucose": rng.randint(75, 110),
            "sleep_duration": round(rng.uniform(5.5, 9.0), 1),
            "mood": rng.randint(1, 5),
            "stress_level": rng.randint(1, 10),
        }
        if astronaut_id == "AST-001" and day == DAYS - 1:
            measurements["heart_rate"] = 110
        for metric, value in measurements.items():
            records.append({
                "measurement_id": f"M-{len(records)+1:05d}",
                "astronaut_id": astronaut_id,
                "mission_id": MISSION_ID,
                "timestamp_utc": timestamp,
                "metric_type": metric,
                "value": float(value),
                "unit": UNITS[metric],
                "source": "synthetic",
                "is_synthetic": True,
                "context": (
                    "Synthetic fasting measurement"
                    if metric == "blood_glucose"
                    else "Synthetic daily measurement"
                ),
            })

df = pd.DataFrame(records)

output_dir = Path(__file__).resolve().parent / "synthetic"
output_dir.mkdir(parents=True, exist_ok=True)

output_file = output_dir / "health_measurements.csv"
df.to_csv(output_file, index=False, encoding="utf-8")

print(f"Generated {len(df)} synthetic measurements")
print(f"Saved to: {output_file}")
