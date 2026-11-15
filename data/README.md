# AstroGuard – Data Engineering

## Overview

This module provides synthetic astronaut health measurements, data validation, cleaning, and automated testing for the AstroGuard health monitoring system.

The processed data is intended to support the Backend, AI, and Frontend modules.

## Dataset

The initial dataset contains:

- 3 fictional astronauts
- 30 days of simulated measurements
- 8 health indicators
- 720 measurement records

All measurements are synthetic and must not be interpreted as actual astronaut health data.

## Directory Structure

```text
data/
├── README.md
├── data_dictionary.md
├── generate_data.py
├── validate_data.py
├── clean_data.py
├── test_data_pipeline.py
├── synthetic/
│   └── health_measurements.csv
└── processed/          # Generated locally
```

## How to Run

Run these commands from the repository root.

**1. Generate synthetic data**

```bash
python data/generate_data.py
```

**2. Validate the dataset**

```bash
python data/validate_data.py
```

**3. Clean the dataset**

```bash
python data/clean_data.py
```

**4. Run automated tests**

```bash
python data/test_data_pipeline.py
```

## Outputs

The cleaning process creates two files:

- `processed/health_measurements_clean.csv` – records that passed the validation and cleaning rules.
- `processed/rejected_records.csv` – rejected records with explanations.

The original dataset is preserved.

## Quality Checks

The current pipeline includes:

- Required field validation
- Duplicate measurement ID detection
- Metric type and unit validation
- Numeric value validation
- Timestamp validation
- Synthetic data labeling checks
- Automated tests with intentionally corrupted records

## Integration

The cleaned dataset can be used by:

- Backend: data ingestion and storage
- AI: exploratory analysis and anomaly detection experiments
- Frontend: health indicator visualizations

The shared data format is documented in `data_dictionary.md`.

## Sleep & Alertness Scenarios (Stage 1A)

A separate, reproducible generator creates fictional sleep sessions, optional
sleep stages, PVT-inspired reaction-time assessments and optional cortisol
observations for four scenarios (stable; shifted schedule; reduced sleep with
slower reactions; insufficient/missing data). It does not modify the legacy
720-measurement dataset.

```bash
python data/generate_sleep_alertness.py        # writes data/synthetic/sleep_alertness/
python -m backend.import_sleep_alertness       # optional: load into the SQLite database
```

The same seed always produces byte-identical files. Data contracts are in
`docs/contracts.md`. All records are labeled synthetic and are not clinically
validated.

## Limitations

- All data is synthetic.
- Generated values are not clinically validated.
- Technical validation does not establish medical safety.
- The current dataset is not suitable for training a clinically reliable predictive model.
- Future development should include additional data quality tests and documented spaceflight-specific research sources.
