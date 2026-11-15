# AstroGuard
AI-powered astronaut health monitoring and early warning system for long-duration space missions.

All health data in this repository is synthetic and for demonstration only.

## Requirements

- Windows 10 or 11
- Python 3.12 ([python.org](https://www.python.org/downloads/) installer; enable the `py` launcher)
- Git

Python packages are listed in [`requirements.txt`](requirements.txt): Streamlit,
Pandas, NumPy, and Plotly. SQLite and `unittest` are part of the Python standard
library.

## Installation (Windows, PowerShell)

Run these commands from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks `Activate.ps1`, allow scripts for the current window only and
activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

From Command Prompt (`cmd.exe`), activate with `.venv\Scripts\activate.bat` instead.

## Running the dashboard

With the virtual environment active:

```powershell
python -m streamlit run app.py
```

Streamlit opens the dashboard at <http://localhost:8501>. Press `Ctrl+C` in the
terminal to stop it.

On first start the app prepares the local database automatically: it creates
`backend/astroguard.db`, cleans `data/synthetic/health_measurements.csv` into
`data/processed/`, and imports the demo measurements. Later starts reuse the
existing database. Both outputs are local files ignored by Git.

To prepare the database without starting the dashboard:

```powershell
python -m backend.bootstrap
```

To rebuild the demo database from scratch, stop the app and delete the database file:

```powershell
Remove-Item backend\astroguard.db
```

## Data validation

```powershell
python data\validate_data.py
echo $LASTEXITCODE
```

The validator exits with status `0` when the dataset passes and `1` when any check
fails, so it can be used in scripts and CI. To validate a different CSV file, pass
its path: `python data\validate_data.py path\to\file.csv`.

## Testing

Run all suites from the repository root with the virtual environment active:

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s backend -v
python data\test_data_pipeline.py
```

`tests/test_startup.py` contains the dashboard startup regression tests. They start
`app.py` headlessly with Streamlit's testing API against a temporary copy of the
repository, so your local database is not modified.

## Module documentation

- [`backend/README.md`](backend/README.md): database, retrieval, and analysis services
- [`data/README.md`](data/README.md): synthetic data generation, validation, and cleaning
- [`ai/README.md`](ai/README.md): health anomaly analysis
