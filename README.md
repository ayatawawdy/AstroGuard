# AstroGuard

Personalized astronaut **sleep and alertness decision support**: a research
prototype for the NASA Space Apps Challenge. See also the
[submission summary](docs/submission_summary.md) and the
[demo script](docs/demo_script.md).

## The problem

Astronauts often sleep less and on shifting schedules, and reduced sleep can
slow reactions during demanding work. Wearable sleep data and short
reaction-time tests exist, but turning them into a clear, *personal* and
*explainable* signal for mission staff is hard. Each person's normal is
different, data is often missing, and a schedule change alone does not mean
someone is impaired.

## Our solution

AstroGuard brings together manual sleep logs, wrist-actigraphy data
(the Actiwatch Spectrum family is the reference device), short PVT-inspired
reaction-time tests and optional cortisol context. It compares each person
only with **their own earlier baseline**. The result is one of four statuses:
**Normal, Attention Needed, Rest Recommended, Insufficient Data**. Each status
comes with its evidence, a task-aware recommendation, and a persistent,
acknowledgeable alert in a Mission Control dashboard.

**Main innovation.** The analysis is personal and fully transparent:
- It uses each person's own baseline.
- Every rule, threshold and supporting record is shown.
- It needs reduced sleep *and* slower reactions together before recommending rest.
- It says plainly when the data is insufficient.

This combines with a Mission Control alert workflow (deduplicated, persistent,
acknowledged by a human) and an adapter that accepts real Actiwatch exports.

## Scientific honesty: what AstroGuard is and is not

1. Actiwatch is an existing wrist-worn actigraphy device. We did not build a wearable.
2. Wrist actigraphy has been used in NASA-supported spaceflight sleep research.
   AstroGuard has **no** NASA endorsement, partnership or system integration.
3. Actiwatch estimates sleep/wake from movement and records light. It does **not**
   measure EEG sleep stages, cortisol or reaction time.
4. The public research recordings come from **people on Earth** (people with chronic
   insomnia and their bed partners), not astronauts.
5. The four astronaut scenarios are **fictional and synthetic**.
6. The reaction test is **PVT-inspired** and not clinically validated.
7. Classifications use **illustrative, transparent rules**. They have not been
   validated, and in particular not with NASA astronaut data.
8. AstroGuard does **not** diagnose fatigue or replace medical judgment.
9. There is **no** continuous real-time monitoring from a physical watch. Stored
   or imported records are evaluated on demand.
10. AstroGuard **never** pauses, stops or interrupts a task. Human operators keep
    control, especially of safety-critical operations.

## Main features

| Area | What works |
|---|---|
| Data | SQLite with legacy tables (720 measurements) plus sleep sessions, optional stages, reaction-time assessments and trials, optional cortisol, actigraphy recordings and epochs, and notification events. All changes are additive and every import can be repeated safely. |
| Actiwatch adapter | Parser for real **Actiware Export (v05.00)** CSVs, verified against the public Zenodo 4961281 dataset. Converts the file's timezone to UTC, handles `NaN`, off-wrist, markers, light, device Sleep/Wake and REST intervals. Derives sleep periods. |
| Synthetic actigraphy | Reproducible fictional Actiwatch-like movement/light epochs for the demo crew, plus an Actiware-*style* scoring heuristic. |
| Analysis | Personal baselines built from earlier observations only. Covers sleep duration (estimated sleep, not time in bed), schedule shift and regularity, naps, reaction time, lapses and false starts, time since waking, and data quality. Outputs one of **Normal / Attention Needed / Rest Recommended / Insufficient Data**, with evidence, rule checks and limitations. |
| Decision support | Task-context-aware wording (Routine Monitoring, Scientific Experiment, Equipment Maintenance, Safety-Critical Operation). Never automatic interruption. |
| Notifications | Persistent SQLite events with deterministic deduplication, an **Acknowledge Alert** button, a full history with evidence, and an optional browser tone. |
| Dashboard | Five sections, with sub-tabs: **Overview & Decision** (Summary, Decision support & evidence), **Sleep** (Sleep monitoring, Log sleep, Actigraphy), **Alertness** (Reaction-time trends, Reaction test), **Alerts** (history and acknowledgment), and **More Data** (Cortisol, Legacy health). A dark NASA-inspired theme is set in `.streamlit/config.toml`. |
| Reaction test | Interactive PVT-*inspired* browser test (10 trials, browser high-resolution timing, no Node/npm). Results are saved and re-evaluated automatically. |

## Architecture

```text
Actiware CSV / synthetic actigraphy ─┐
Manual sleep log (dashboard) ────────┼─► validation & UTC normalization ─► SQLite
Synthetic demo scenarios (CSV) ──────┤        (backend/sleep_alertness.py,      │
Browser reaction-time test ──────────┘         backend/actigraphy.py)            │
                                                                                 ▼
            backend/alertness_analysis.py  ◄── personal baseline (earlier data only)
                         │  status + evidence + task-context recommendation
                         ▼
            backend/notifications.py  ── deduplicated, persistent, acknowledgeable events
                         │
            backend/decision_support.py  (evaluate_and_notify: one pipeline for all inputs)
                         ▼
                      app.py  (Streamlit Mission Control)
```

| File | Role |
|---|---|
| `backend/database.py` | Schema (legacy + Stage 1A + decision-support tables), `ASTROGUARD_DB_PATH` override |
| `backend/bootstrap.py` | Startup: legacy import (unchanged) + sleep/alertness demo + synthetic actigraphy |
| `backend/sleep_alertness.py` | Validated insert/retrieve for sleep, stages, assessments, trials, cortisol |
| `backend/actigraphy.py` | Actiware parser, scoring heuristic, sleep-period derivation, synthetic epochs |
| `backend/alertness_analysis.py` | Transparent, configurable personalized analysis |
| `backend/notifications.py` | Alert persistence, deduplication, acknowledgment, history |
| `backend/decision_support.py` | Orchestration, browser test saving, mission discovery, demo registry |
| `components/reaction_test/index.html` | Plain-HTML/JS Streamlit component (no build step) |
| `data/generate_sleep_alertness.py` | Seeded generator for the four fictional scenarios |
| `docs/contracts.md` | Data contracts and result structure |
| `docs/demo_script.md` | 3–5 minute demo script and answers for judges |

The `ai/` package (teammates' health anomaly analysis) is unchanged and still
powers the **Legacy Health** tab.

## Requirements

- Windows 10 or 11
- Python 3.12 ([python.org](https://www.python.org/downloads/) installer; enable the `py` launcher)
- Git

Python packages are listed in [`requirements.txt`](requirements.txt): Streamlit,
Pandas, NumPy, and Plotly. SQLite and `unittest` are part of the Python standard
library. No Node.js, npm or cloud service is needed.

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

```powershell
python -m streamlit run app.py
```

Streamlit opens <http://localhost:8501>. Press `Ctrl+C` to stop.

On first start the app prepares the database automatically. It creates
`backend/astroguard.db`, cleans and imports the 720 legacy measurements, loads
the four sleep & alertness scenarios, and generates synthetic actigraphy. Later
starts reuse the database. Repeated starts never duplicate records.

To prepare the database without the dashboard: `python -m backend.bootstrap`.

**Fresh demo database (recommended before presenting).** Records you enter
live, such as sleep logs, reaction tests and acknowledgments, are persistent. To
present from a pristine state without deleting anything, point the app at a new
file:

```powershell
$env:ASTROGUARD_DB_PATH = "$PWD\demo_presentation.db"
python -m streamlit run app.py
Remove-Item Env:\ASTROGUARD_DB_PATH   # back to backend\astroguard.db afterwards
```

## Using the demo scenarios

The dashboard opens on **Scenario 1**. Use the four scenario buttons at the top
(or the *Select astronaut* list):

| Button | Fictional astronaut | Expected status (from the real pipeline) |
|---|---|---|
| Scenario 1 · Normal sleep and alertness | AST-S01 | Normal |
| Scenario 2 · Shifted sleep schedule | AST-S02 | Attention Needed. The schedule changed, and the app says explicitly that this alone does not show reduced concentration. |
| Scenario 3 · Reduced sleep, slower reactions | AST-S03 | **Rest Recommended**, which creates a persistent alert |
| Scenario 4 · Insufficient data | AST-S04 | Insufficient Data (it explains what is missing) |

**Interactive testing uses a separate profile.**
- Reaction tests and sleep logs are saved only to the **🧪 Live test profile** (`LIVE-USER-01`, session `LIVE-INTERACTIVE-TESTS`). Those records are real (`is_synthetic = 0`) and are evaluated only against that profile's own history.
- The four demo scenarios are protected: the dashboard and the backend refuse interactive entries for them, and they are always evaluated on their predefined synthetic records.
- Real records that were stored on a demo profile before this protection existed are kept in the database, listed and labeled, and excluded from the scenario's result.
- A new live profile honestly shows *Insufficient Data* until it has enough of its own tests and sleep logs. No sleep data is invented.

Select **Mission task context**, for example *Safety-Critical Operation*, to see
the recommendation wording change. The status never changes with task context.
Press **Acknowledge Alert** to save an acknowledgment. The **🔔 Alerts** section
shows the full history and evidence. *Alert created* is when AstroGuard first evaluated
the evidence; *Data observed up to* is the time of the latest underlying observation,
which for the demo crew is historical (October 2026). See [`docs/demo_script.md`](docs/demo_script.md).

## Real Actiwatch data (optional)

The public dataset contains real Actiware CSV exports from **people on Earth,
not astronauts**. It is not stored in Git.

- **Citation:** Angelova M., Kusmakar S., Karmakar C., Zhu Y., Shelyag S., Drummond S.,
  Ellis J. (2021). *Chronic insomnia and bed partner actigraphy data*. Zenodo
  [record 4961281](https://zenodo.org/records/4961281), doi
  [10.5061/dryad.b8gtht7bh](https://doi.org/10.5061/dryad.b8gtht7bh), licence CC0 1.0.
- **What the authors describe:** seven nights of wrist actigraphy from people with
  chronic insomnia and their bed partners, recorded with a Respironics Actiwatch
  Spectrum Pro and Actiware software in 60-second epochs.
- **The downloaded archive** contains 86 Actiware CSV files. Their headers record
  Australian local time (UTC+10:00 or UTC+11:00).
- **What it does not contain:** reaction-time, cortisol or alertness outcomes.

To use it:

```powershell
New-Item -ItemType Directory -Force data\external\zenodo_4961281
Invoke-WebRequest "https://zenodo.org/api/records/4961281/files/Supplementary_Material_Final.zip/content" -OutFile data\external\zenodo_4961281\Supplementary_Material_Final.zip
python -m backend.actigraphy data\external\zenodo_4961281\Supplementary_Material_Final.zip --limit 2
```

Alternatively, in the dashboard open **🌙 Sleep → ⌚ Actigraphy (recorded)** and use
**Import 2 participants from the local Zenodo 4961281 archive**, or upload an
Actiware CSV. Afterwards the **⌚ Real Actiwatch research data** button selects the
imported data. The Actigraphy view shows the dataset citation and link, the device,
a REAL/SYNTHETIC badge, activity, light, device sleep/wake, epoch length and
missing-data indicators. Imported participants appear as separate
research profiles `RES-<code>`, never as fictional astronauts. Their records are
labeled real (`is_synthetic = 0`). Name, gender, file path and serial-number
header fields are not stored. Because the dataset has no reaction-time data,
these profiles correctly show *Insufficient Data*.

## Testing

Run all suites from the repository root:

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s backend -v
python data\test_data_pipeline.py
```

All tests use temporary databases. `tests/test_startup.py` starts the app on
a temporary repository copy. `tests/test_dashboard.py` drives the full Mission
Control flow (scenarios, alerts, deduplication, acknowledgment, sleep log)
against a temporary database. The real-dataset test runs only if the Zenodo
archive is present locally.

## Data sources and provenance

| Data | Origin | Label |
|---|---|---|
| Legacy health measurements (720) | Synthetic, `data/generate_data.py` | `source = synthetic` |
| Demo crew AST-S01–S04 | Synthetic, `data/generate_sleep_alertness.py` (seed 20261001) | `is_synthetic = 1` |
| Demo actigraphy | Synthetic, `backend/actigraphy.py` (seed 20261002) | `data_origin = synthetic` |
| Research participants `RES-*` | Real Actiware exports (Zenodo 4961281, CC0), Earth participants | `actiwatch_export`, `is_synthetic = 0` |
| Dashboard entries (sleep logs, reaction tests) | Entered by the user | `manual` / `app`, `is_synthetic = 0` |

Every evaluation carries a badge: **HISTORICAL / SYNTHETIC DEMO DATA** (demo crew),
**REAL RESEARCH DATA (EARTH PARTICIPANT)** (`RES-*`) or **REAL / USER-ENTERED DATA**
(live test profile).

## Limitations

- **Actiwatch / actigraphy.**
  - Actigraphy estimates sleep and wake from wrist movement. It does not measure REM or deep sleep, EEG, cortisol, reaction time or heart-rate variability.
  - AstroGuard imports exported files. It does not stream data from the watch, and the watch does not beep, vibrate or receive commands from AstroGuard.
  - The Actiware-style heuristic is illustrative and is not the vendor algorithm.
  - Derived sleep uses the export's REST and REST-S intervals, so totals can differ slightly from Actiware's own summary.
  - Timezones use each file's stated fixed offset. A daylight-saving change in the middle of a recording is not handled.
  - The synthetic Stage 1A sleep-stage records are never displayed, because Actiwatch does not measure stages.
- **Reaction-time test.**
  - It is a short demonstration inspired by the PVT: 10 trials, a 3 s timeout, and lapses counted at 500 ms or more.
  - It is not a validated PVT and is not comparable to the standard 10-minute PVT.
  - Browser timing is still affected by display, input and operating-system latency.
- **Cortisol.** It is optional context only. It never changes the status, and values are not converted between units.
- **No NASA integration.** AstroGuard is an independent hackathon prototype. It is not connected to any NASA system and has not been validated with astronaut data.
- **Rest recommendations.**
  - Recommendations are advisory and come from illustrative, configurable heuristics (see `docs/contracts.md`), not clinical thresholds.
  - The evaluation point is the latest stored observation, so historical data is evaluated at the time it describes. That is not continuous real-time monitoring.
  - Recommendations never interrupt work.

## Module documentation

- [`docs/contracts.md`](docs/contracts.md): data contracts, analysis result, notification events
- [`docs/demo_script.md`](docs/demo_script.md): presentation script and judge Q&A
- [`backend/README.md`](backend/README.md): legacy database, retrieval, and analysis services
- [`data/README.md`](data/README.md): synthetic data generation, validation, and cleaning
- [`ai/README.md`](ai/README.md): health anomaly analysis (Legacy Health tab)
