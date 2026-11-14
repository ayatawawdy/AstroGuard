# AstroGuard – Health Data Dictionary

## 1. Purpose

This document defines the structure, types, and units of health measurements used in AstroGuard.

The data schema supports health monitoring, data processing, visualization, and future AI-based anomaly detection.

## 2. Measurement Data Schema

Each record represents one health measurement.

| Field | Data Type | Description |
|---|---|---|
| measurement_id | String | Unique identifier for each measurement |
| astronaut_id | String | Identifier of the astronaut |
| mission_id | String | Identifier of the space mission |
| timestamp_utc | String (ISO 8601) | Date and time of measurement in UTC |
| metric_type | String | Type of health indicator |
| value | Float | Recorded measurement value |
| unit | String | Unit of measurement |
| source | String | manual, device, or synthetic |
| is_synthetic | Boolean | Indicates whether the measurement is simulated |
| context | String / Null | Optional measurement context or notes |

## 3. Supported Health Indicators

| Metric Type | Unit | Description |
|---|---|---|
| heart_rate | bpm | Heart rate |
| systolic_bp | mmHg | Systolic blood pressure |
| diastolic_bp | mmHg | Diastolic blood pressure |
| body_temperature | °C | Body temperature |
| blood_glucose | mg/dL | Blood glucose level |
| sleep_duration | hours | Total sleep duration |
| mood | 1–5 | Self-reported mood score |
| stress_level | 1–10 | Self-reported stress level |

Mood and stress scales are application-defined self-report scales and must be explained consistently in the user interface.

## 4. Data Validation Rules

- Each measurement must have a unique measurement ID.
- All required fields must be present.
- Measurements must contain valid numeric values.
- Timestamps must use UTC ISO 8601 format.
- Future measurement timestamps are not accepted.
- Units must match the specified metric type.
- Duplicate records should be detected.
- Missing values must not be silently replaced with zero.
- Demonstration data must be explicitly marked as synthetic.
- Medical alert thresholds must be defined separately from technical data validation rules.

## 5. Data Sources

AstroGuard may use:

1. Manually entered astronaut health measurements.
2. Measurements imported from compatible medical devices.
3. Synthetic datasets generated for development and demonstration.
4. Publicly available research datasets, where permitted by their licenses and data-use conditions.

Synthetic data must never be presented as real astronaut medical records.

## 6. Future Extensions

Possible additional indicators include:

- Exercise duration and physical activity
- Radiation exposure measurements
- Fatigue and reported symptoms
- Oxygen saturation
- Additional mission-specific health indicators

These indicators will be added only after confirming suitable measurement methods and data requirements.

## 7. Integration

This schema serves as the proposed common data format for:

- Data Engineering: data generation, cleaning, and validation.
- Backend: data storage and retrieval.
- AI: anomaly detection and trend analysis.
- Frontend: health monitoring dashboards and visualizations.

The schema must be reviewed with the Backend and AI developers before implementation.
