
from contextlib import closing

from backend.database import get_connection
import math
from datetime import datetime, timezone
from uuid import uuid4


def get_measurements(
    astronaut_id: str,
    metric_type: str | None = None,
    limit: int = 100
) -> list[dict]:
    """
    Retrieve health measurements for an astronaut.

    Optional filters:
    - metric_type: specific health indicator
    - limit: maximum number of returned records
    """

    if not astronaut_id.strip():
        raise ValueError("Astronaut ID is required")

    if not 1 <= limit <= 1000:
        raise ValueError("Limit must be between 1 and 1000")

    query = """
        SELECT *
        FROM measurements
        WHERE astronaut_id = ?
    """

    params: list[str | int] = [astronaut_id]

    if metric_type is not None:
        query += " AND metric_type = ?"
        params.append(metric_type)

    query += """
        ORDER BY timestamp_utc DESC, measurement_id DESC
        LIMIT ?
    """
    params.append(limit)

    with closing(get_connection()) as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(row) for row in rows]


def get_astronauts() -> list[dict]:
    """Retrieve all registered astronauts."""

    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT astronaut_id, display_name
            FROM astronauts
            ORDER BY astronaut_id
            """
        ).fetchall()

    return [dict(row) for row in rows]


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


def add_measurement(
    astronaut_id: str,
    mission_id: str,
    metric_type: str,
    value: float,
    source: str,
    context: str | None = None
) -> str:
    """Add a new health measurement to the database."""

    if not astronaut_id.strip() or not mission_id.strip():
        raise ValueError("Astronaut and mission IDs are required")

    if metric_type not in METRIC_UNITS:
        raise ValueError("Unknown health indicator")

    if source not in ("manual", "device", "synthetic"):
        raise ValueError("Invalid measurement source")

    if isinstance(value, bool):
        raise ValueError("Measurement value must be numeric")

    try:
        numeric_value = float(value)
    except (ValueError, TypeError):
        raise ValueError("Measurement value must be numeric")

    if not math.isfinite(numeric_value):
        raise ValueError("Measurement value must be finite")

    # Basic input checks, not clinical alert thresholds
    if metric_type == "mood":
        if not numeric_value.is_integer() or not 1 <= numeric_value <= 5:
            raise ValueError("Mood must be an integer from 1 to 5")
    elif metric_type == "stress_level":
        if not numeric_value.is_integer() or not 1 <= numeric_value <= 10:
            raise ValueError("Stress must be an integer from 1 to 10")
    elif metric_type == "sleep_duration":
        if not 0 <= numeric_value <= 24:
            raise ValueError("Sleep duration must be between 0 and 24")
    elif numeric_value <= 0:
        raise ValueError("Measurement value must be positive")

    measurement_id = f"M-{uuid4().hex}"
    timestamp = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")

    unit = METRIC_UNITS[metric_type]
    is_synthetic = int(source == "synthetic")

    with closing(get_connection()) as conn:
        with conn:
            astronaut_exists = conn.execute(
                "SELECT 1 FROM astronauts WHERE astronaut_id = ?",
                (astronaut_id,)
            ).fetchone()

            mission_exists = conn.execute(
                "SELECT 1 FROM missions WHERE mission_id = ?",
                (mission_id,)
            ).fetchone()

            if not astronaut_exists or not mission_exists:
                raise ValueError("Unknown astronaut or mission")

            conn.execute(
                """
                INSERT INTO measurements (
                    measurement_id, astronaut_id, mission_id,
                    timestamp_utc, metric_type, value, unit,
                    source, is_synthetic, context
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    measurement_id, astronaut_id, mission_id,
                    timestamp, metric_type, numeric_value, unit,
                    source, is_synthetic, context
                )
            )

    return measurement_id



def update_measurement(
    measurement_id: str,
    new_value: float
) -> bool:
    """Update the value of an existing health measurement."""

    if not measurement_id.strip():
        raise ValueError("Measurement ID is required")

    if isinstance(new_value, bool):
        raise ValueError("Measurement value must be numeric")

    try:
        numeric_value = float(new_value)
    except (ValueError, TypeError):
        raise ValueError("Measurement value must be numeric")

    if not math.isfinite(numeric_value):
        raise ValueError("Measurement value must be finite")

    with closing(get_connection()) as conn:
        with conn:
            measurement = conn.execute(
                """
                SELECT metric_type
                FROM measurements
                WHERE measurement_id = ?
                """,
                (measurement_id,)
            ).fetchone()

            if measurement is None:
                raise ValueError("Measurement not found")

            metric_type = measurement["metric_type"]

            # Technical validation, not clinical thresholds
            if metric_type == "mood":
                if (
                    not numeric_value.is_integer()
                    or not 1 <= numeric_value <= 5
                ):
                    raise ValueError(
                        "Mood must be an integer from 1 to 5"
                    )

            elif metric_type == "stress_level":
                if (
                    not numeric_value.is_integer()
                    or not 1 <= numeric_value <= 10
                ):
                    raise ValueError(
                        "Stress must be an integer from 1 to 10"
                    )

            elif metric_type == "sleep_duration":
                if not 0 <= numeric_value <= 24:
                    raise ValueError(
                        "Sleep duration must be between 0 and 24"
                    )

            elif numeric_value <= 0:
                raise ValueError(
                    "Measurement value must be positive"
                )

            conn.execute(
                """
                UPDATE measurements
                SET value = ?
                WHERE measurement_id = ?
                """,
                (numeric_value, measurement_id)
            )

    return True



def delete_measurement(measurement_id: str) -> bool:
    """Delete an existing measurement by its unique ID."""

    if not measurement_id.strip():
        raise ValueError("Measurement ID is required")

    with closing(get_connection()) as conn:
        with conn:
            result = conn.execute(
                """
                DELETE FROM measurements
                WHERE measurement_id = ?
                """,
                (measurement_id,)
            )

            return result.rowcount > 0
