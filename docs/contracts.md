# AstroGuard – Sleep & Alertness Data Contracts (Stage 1A)

Status: **Stage 1A data foundation + Stage 1B decision support.** Sections 1–6 and 8
define the stored records. Sections 7, 7a and 9 define notifications, the analysis
result and actigraphy. All thresholds are illustrative heuristics, not clinical
criteria, and nothing here interrupts astronaut tasks.

Audience: Backend, AI and Dashboard developers building on these records.

| Item | Location |
|---|---|
| Schema (SQLite DDL) | `backend/database.py` → `SLEEP_ALERTNESS_SCHEMA` |
| Insert / retrieval API | `backend/sleep_alertness.py` |
| Synthetic dataset importer | `backend/import_sleep_alertness.py` |
| Synthetic scenario generator | `data/generate_sleep_alertness.py` |
| Generated dataset | `data/synthetic/sleep_alertness/` |
| Analysis / notifications / orchestration | `backend/alertness_analysis.py`, `backend/notifications.py`, `backend/decision_support.py` |
| Actigraphy adapter | `backend/actigraphy.py` |
| Tests | `tests/test_sleep_alertness*.py`, `tests/test_alertness_analysis.py`, `tests/test_notifications.py`, `tests/test_actigraphy.py`, `tests/test_dashboard.py` |

The legacy contract (`astronauts`, `missions`, `measurements`; see
`data/data_dictionary.md`) is unchanged. The legacy `sleep_duration`
measurement remains a separate, older metric and is not derived from or
synchronized with `sleep_sessions`.

---

## 1. Principles

1. **Time in bed is not sleep.** `bedtime_utc` → `wake_time_utc` is the time-in-bed
   interval. Actual sleep is only `estimated_sleep_minutes`, which is optional
   and always an *estimate* (self-reported or device-estimated).
2. **Estimates are never presented as clinical measurements.** Sleep stages
   are stored with `is_estimated = 1` (enforced by the schema). Consumer-grade or
   synthetic staging is not polysomnography.
3. **Explicit provenance on every record.** Each row carries `data_origin` and
   `is_synthetic`. Synthetic data must always be labeled as synthetic in any
   UI, export or analysis.
4. **Missing data stays missing.** No imputation, interpolation or zero-filling.
   Absent values are `NULL`, absent records are simply absent, and reasons may
   be recorded in `missing_data_notes`.
5. **Cortisol is context only.** It is never a direct or standalone indicator
   of alertness, and no rule may use it alone.
6. **No medical thresholds in Stage 1A.** Validation rules are technical
   (formats, intervals, consistency). The PVT lapse/false-start parameters are
   *protocol scoring definitions* stored with each assessment, not alert levels.
7. **No clinical diagnosis or automated termination of critical astronaut tasks**,
   now or in future stages that consume these contracts.

### Timestamps

* Input: ISO 8601 with an explicit offset (`Z`, `+02:00`) or an aware
  `datetime`. Naive timestamps raise `ValueError`.
* Stored: normalized UTC text `YYYY-MM-DDTHH:MM:SSZ` (fixed width, so text
  order equals chronological order).
* Whole-second precision. Sub-second timestamps are rejected (not truncated);
  millisecond precision belongs in trial-level `reaction_time_ms`.
* Timestamps in the future are rejected for stored records.
* The schema also rejects unparseable text and reversed intervals
  (`julianday()` CHECK constraints).

### Provenance vocabulary

| Table | `data_origin` values | Rule |
|---|---|---|
| `sleep_sessions` | `manual`, `wearable`, `synthetic` | `synthetic` ⇒ `is_synthetic = 1` |
| `sleep_stages.source` | `wearable`, `synthetic` | always equals the parent session origin |
| `alertness_assessments` | `app`, `synthetic` | `synthetic` ⇒ `is_synthetic = 1` |
| `cortisol_measurements` | `manual`, `synthetic` | `synthetic` ⇒ `is_synthetic = 1` |

`data_origin` describes the *channel* a record represents; `is_synthetic`
says whether it was fabricated. A **simulated wearable recording** is
therefore `data_origin = 'wearable'`, `is_synthetic = 1`. This lets demos
exercise wearable-specific rules (sensor quality, stages) without a physical
sensor, while still being labeled synthetic. Real data always has
`is_synthetic = 0`.

---

## 2. Sleep sessions – `sleep_sessions`

One row per sleep period (main sleep or nap) from one origin. A manual log
and a wearable recording of the same night are two rows.

| Field | Type | Required | Description |
|---|---|---|---|
| `sleep_session_id` | TEXT PK | yes | Caller-supplied stable ID (imports) or generated `SLP-<uuid>` |
| `astronaut_id` | TEXT FK | yes | → `astronauts` |
| `mission_id` | TEXT FK | yes | → `missions` |
| `sleep_type` | TEXT | yes | `main` or `nap` |
| `bedtime_utc` | TEXT | yes | Start of time in bed (UTC) |
| `wake_time_utc` | TEXT | yes | End of time in bed (UTC); must be after `bedtime_utc` |
| `estimated_sleep_minutes` | REAL | no | Estimated actual sleep; 0 ≤ value ≤ time in bed |
| `data_origin` | TEXT | yes | `manual`, `wearable`, `synthetic` |
| `is_synthetic` | INTEGER 0/1 | yes | Synthetic label |
| `self_reported_quality` | INTEGER | no | Application-defined 1 (very poor) – 5 (very good) |
| `sensor_quality` | TEXT | wearable: yes; manual: must be NULL | `good`, `fair`, `poor`, `unusable` |
| `sensor_coverage_fraction` | REAL | no; manual: must be NULL | 0–1 fraction of the in-bed interval with usable signal |
| `missing_data_notes` | TEXT | no | Why data is missing or degraded |
| `notes` | TEXT | no | Free-text context |

Derived on retrieval (not stored): `time_in_bed_minutes`.

**Cross-midnight sessions** are ordinary intervals in UTC (e.g.
`2026-10-01T22:45:00Z` → `2026-10-02T06:15:00Z`). Do not split them by
calendar day. Window queries return any session that *overlaps* the window.

**Validation (technical, not clinical)**

* Session length is > 0 and ≤ 24 hours.
* `estimated_sleep_minutes` must not exceed time in bed.
* Manual logs cannot carry sensor fields. Wearable sessions must state `sensor_quality`.
* **Conflicting sensor observations are rejected:** an `unusable` signal or a
  coverage of `0` together with a sleep estimate. Two sessions of the **same
  astronaut and same origin** with overlapping intervals are also rejected
  (touching intervals are allowed). Different origins may overlap.
* **Duplicates:** natural key `(astronaut_id, data_origin, bedtime_utc)` is UNIQUE.
  An identical re-insert (same ID, or same natural key) returns the existing ID.
  Different content under an existing ID or natural key raises `ValueError`.

## 3. Sleep stages – `sleep_stages` (optional)

Estimated stage intervals for a wearable or synthetic session. A session
without stage rows is valid; retrieval then reports
`stage_data_status = "unavailable"`.

| Field | Type | Description |
|---|---|---|
| `sleep_session_id` | TEXT FK, PK part | → `sleep_sessions` |
| `start_utc` | TEXT, PK part | Stage start (UTC) |
| `end_utc` | TEXT | Stage end (UTC); after `start_utc` |
| `stage_type` | TEXT | `awake`, `light`, `deep`, `rem`, `unknown` |
| `source` | TEXT | `wearable` or `synthetic`; equals parent session origin |
| `is_estimated` | INTEGER | Always `1` (schema-enforced) |
| `is_synthetic` | INTEGER 0/1 | Equals parent session flag |

Rules: stages lie inside their session, do not overlap, and are stored
atomically per call. Manual logs cannot have stages. Stage totals are not
required to equal `estimated_sleep_minutes` (device algorithms may differ).
Stages may leave gaps (unscored periods).

> Sleep-stage values are device or synthetic **estimates**. They must not be
> labeled or displayed as clinically verified sleep staging.

## 4. Alertness assessments – `alertness_assessments` + `reaction_time_trials`

Records for a future **PVT-inspired** reaction-time test. Stage 1A stores and
validates results only; the interactive test is not implemented.

### `alertness_assessments`

| Field | Type | Description |
|---|---|---|
| `assessment_id` | TEXT PK | Caller-supplied or `ALR-<uuid>` |
| `astronaut_id`, `mission_id` | TEXT FK | References |
| `assessment_utc` | TEXT | Test start (UTC); `(astronaut_id, assessment_utc)` UNIQUE |
| `protocol_version` | TEXT | Nonempty protocol identifier, e.g. `astroguard-pvt-inspired-v0.1` |
| `timing_method` | TEXT | `software_monotonic_clock`, `browser_event_timestamp`, `synthetic_generator`, `unknown` |
| `completion_status` | TEXT | `completed` or `incomplete` |
| `data_origin` | TEXT | `app` or `synthetic` |
| `is_synthetic` | INTEGER 0/1 | `synthetic_generator` timing requires `1` |
| `lapse_threshold_ms` | REAL | Protocol scoring parameter (default 500) |
| `false_start_threshold_ms` | REAL | Protocol scoring parameter (default 100); < lapse threshold |
| `trial_count` | INTEGER | Computed |
| `response_count` | INTEGER | Computed: responses that are not false starts |
| `lapse_count` | INTEGER | Computed |
| `false_start_count` | INTEGER | Computed |
| `mean_rt_ms`, `median_rt_ms`, `sd_rt_ms`, `min_rt_ms`, `max_rt_ms` | REAL | Computed over responses that are not false starts; NULL if none (`sd` needs ≥ 2) |
| `self_reported_fatigue` | INTEGER | Optional, application-defined 1 (fully alert) – 7 (exhausted) |
| `notes` | TEXT | Optional |

**Summary statistics are computed by the backend from the trials.** Callers
cannot supply them, so a stored summary always matches its stored trials.

### `reaction_time_trials`

| Field | Type | Description |
|---|---|---|
| `assessment_id` | TEXT FK, PK part | → `alertness_assessments` |
| `trial_index` | INTEGER, PK part | 1-based, unique per assessment |
| `interstimulus_interval_ms` | REAL | Optional, > 0 |
| `reaction_time_ms` | REAL | Stimulus-to-response time; NULL = no response or responded before stimulus |
| `responded_before_stimulus` | INTEGER 0/1 | Response before stimulus (RT must be NULL) |
| `outcome` | TEXT | Computed: `valid`, `lapse`, `false_start` |

Trial classification, in order:

1. `responded_before_stimulus` → `false_start`
2. no response (`reaction_time_ms` NULL) → `lapse` (missed response)
3. RT < `false_start_threshold_ms` → `false_start`
4. RT ≥ `lapse_threshold_ms` → `lapse`
5. otherwise → `valid`

The 500 ms / 100 ms defaults follow common PVT research conventions. They are
**not validated for AstroGuard** and are not decision thresholds. The
assessment and its trials are stored atomically. An identical re-insert is a
no-op. Different trials under an existing assessment raise `ValueError`.

## 5. Optional cortisol – `cortisol_measurements`

| Field | Type | Description |
|---|---|---|
| `cortisol_id` | TEXT PK | Caller-supplied or `CRT-<uuid>` |
| `astronaut_id`, `mission_id` | TEXT FK | References |
| `sample_utc` | TEXT | Sample collection time (UTC) |
| `value` | REAL | > 0 |
| `unit` | TEXT | Explicit: `nmol/L` or `ug/dL`. Stored as given; **no conversion** |
| `sampling_method` | TEXT | `saliva`, `blood_serum`, `blood_plasma`, `other` |
| `minutes_since_awakening` | REAL | Optional, ≥ 0 |
| `sleep_session_id` | TEXT FK | Optional link to the preceding sleep session |
| `data_origin` | TEXT | `manual` (entered from an assay report) or `synthetic` |
| `is_synthetic` | INTEGER 0/1 | Synthetic label |
| `notes` | TEXT | Optional |

If `sleep_session_id` is given, it must belong to the same astronaut and
mission, and the sample must be at or after its wake time.
`minutes_since_awakening` is derived from the wake time (or must agree with
it within 1 minute). Natural key `(astronaut_id, sample_utc, sampling_method)`
is UNIQUE.

> Cortisol varies strongly with time of day, time since waking, sampling
> method and assay. It must never be used as a direct or standalone alertness
> indicator, and values in different units must not be compared without a
> documented conversion.

---

## 6. Python interface – `backend/sleep_alertness.py`

All functions accept `db_path=` (omit it for the configured database). The
database must already be initialized (`backend.database.initialize_database`).
Invalid input raises `ValueError`. `sqlite3.Error` propagates.

```python
add_sleep_session(astronaut_id, mission_id, bedtime_utc, wake_time_utc, *,
                  data_origin, is_synthetic=False, sleep_type="main",
                  estimated_sleep_minutes=None, self_reported_quality=None,
                  sensor_quality=None, sensor_coverage_fraction=None,
                  missing_data_notes=None, notes=None,
                  sleep_session_id=None, db_path=None) -> str

add_sleep_stages(sleep_session_id, stages, *, db_path=None) -> int   # rows inserted
    # stages: [{"stage_type", "start_utc", "end_utc"}, ...]

add_alertness_assessment(astronaut_id, mission_id, assessment_utc, trials, *,
                         protocol_version, timing_method, data_origin="app",
                         is_synthetic=False, completion_status="completed",
                         lapse_threshold_ms=500.0, false_start_threshold_ms=100.0,
                         self_reported_fatigue=None, notes=None,
                         assessment_id=None, db_path=None) -> str
    # trials: [{"trial_index"?, "interstimulus_interval_ms"?, "reaction_time_ms",
    #           "responded_before_stimulus"?}, ...]

add_cortisol_measurement(astronaut_id, mission_id, sample_utc, value, unit,
                         sampling_method, *, data_origin="manual", is_synthetic=False,
                         minutes_since_awakening=None, sleep_session_id=None,
                         notes=None, cortisol_id=None, db_path=None) -> str

get_sleep_sessions(astronaut_id, *, mission_id=None, start_utc=None, end_utc=None,
                   data_origin=None, sleep_type=None, include_stages=False,
                   db_path=None) -> list[dict]
get_sleep_stages(sleep_session_id, *, db_path=None) -> list[dict]
get_alertness_assessments(astronaut_id, *, mission_id=None, start_utc=None,
                          end_utc=None, include_trials=False, db_path=None) -> list[dict]
get_reaction_time_trials(assessment_id, *, db_path=None) -> list[dict]
get_cortisol_measurements(astronaut_id, *, mission_id=None, start_utc=None,
                          end_utc=None, db_path=None) -> list[dict]
```

Retrieval contract:

* Results are ordered **chronologically ascending**. The legacy
  `get_measurements` is descending; that is unchanged.
* Rows are plain dicts using the column names above. `is_synthetic`,
  `is_estimated` and `responded_before_stimulus` are returned as Python
  `bool`. The legacy API returns `is_synthetic` as 0/1.
* Window filters use the half-open interval `[start_utc, end_utc)`. Sleep sessions match when
  their in-bed interval overlaps the window; other records match on their timestamp.
* Unknown astronaut/mission filters return `[]`. Blank or non-string IDs raise `ValueError`.
* Results are always scoped to one astronaut, and optionally to one mission.
* No row limit is applied. Callers should pass time windows for large datasets.

Importing the synthetic dataset (not run by dashboard startup):

```python
from backend.import_sleep_alertness import import_sleep_alertness
import_sleep_alertness()             # -> {table: {"inserted": n, "skipped": n}}
```

The whole import runs in one transaction (all or nothing), accepts only synthetic rows, and is safe to
re-run. It never overwrites existing rows, including astronaut display names.

---

## 7. Rest notifications – `rest_notification_events` (implemented)

Implemented in `backend/notifications.py` and created through
`backend/decision_support.evaluate_and_notify()`. Only `attention_needed` and
`rest_recommended` results create events. `normal` and `insufficient_data`
are shown live but are not stored as alerts.

| Field | Type | Description |
|---|---|---|
| `event_id` | TEXT PK | `ALERT-` + first 20 hex characters of `evidence_key` (deterministic) |
| `astronaut_id`, `mission_id` | TEXT FK | Scope |
| `created_utc` | TEXT | When the event was first stored |
| `evaluated_at_utc` | TEXT | Evaluation point (latest stored observation used) |
| `alertness_status` | TEXT | `attention_needed` or `rest_recommended` (schema also allows the other two) |
| `severity` | TEXT | `advisory` (attention) or `warning` (rest). No "critical"/"stop work" level exists |
| `title` | TEXT | `ATTENTION NEEDED` / `REST RECOMMENDED` |
| `explanation`, `suggested_action` | TEXT | Wording at creation time |
| `task_context` | TEXT | Task context selected at creation (wording only) |
| `evidence_json` | TEXT | Observations, baseline comparisons, rule checks, record IDs, data issues |
| `evidence_key` | TEXT | SHA-256 of astronaut, mission, status, analysis version, recent sleep IDs, recent assessment IDs |
| `source_assessment_id` | TEXT | Latest assessment used, if any |
| `analysis_version` | TEXT | e.g. `astroguard-alertness-heuristic-0.1` |
| `is_synthetic_input` | INTEGER 0/1 | Any synthetic input record |
| `acknowledged`, `acknowledged_utc`, `acknowledged_by` | | Acknowledgment state (schema-checked consistency) |

**Deduplication:**
- Re-evaluating the same stored evidence (Streamlit reruns, reopening the app,
  changing the task context) maps to the same `event_id` and inserts nothing.
- A new assessment, new sleep record or changed status produces a new event.

**Acknowledgment** (`acknowledge_notification(event_id, astronaut_id)`):
- The astronaut must match the event.
- It is idempotent: the first acknowledgment time is kept.
- The event is never deleted.
- Acknowledgment does not mean the astronaut is rested or safe.

Constraints that still apply:
- Recommendations are **advisory** and never stop, block or terminate tasks.
- No diagnosis, and no cortisol-only rules.
- Missing data yields `insufficient_data`, never a silent "normal".
- Synthetic inputs are labeled.

## 7b. Demo isolation and the live test profile

- **Profiles.** `backend/decision_support.py` classifies every profile as `demo`
  (AST-S01–S04), `live` (`LIVE-USER-01` / `LIVE-INTERACTIVE-TESTS`),
  `research` (`RES-*`) or `legacy`.
- **Where interactive entries go.** `save_browser_assessment()` and
  `save_manual_sleep_log()` accept only the live profile and session. Any other
  target raises `ValueError` and nothing is stored.
- **How demo profiles are evaluated.** `evaluate_and_notify()` calls
  `analyze_alertness(..., synthetic_only=True)`. The input is restricted to
  records labeled synthetic; the algorithm and thresholds are unchanged.
  Excluded real records are reported in `excluded_record_ids`, and `input_scope`
  is `synthetic_only`. Excluded records are never deleted or modified.
- **What startup adds.** `ensure_live_profile()` registers only the astronaut
  and mission rows (`INSERT OR IGNORE`). No measurements are created.

## 7a. Analysis result – `backend/alertness_analysis.analyze_alertness()`

```python
analyze_alertness(astronaut_id, mission_id, *, task_context="routine_monitoring",
                  config=DEFAULT_CONFIG, db_path=None) -> dict
```

Result keys:
- **Identity and timing:** `astronaut_id`, `mission_id`, `analysis_version`,
  `analyzed_at_utc`, `evaluated_at_utc` (the latest stored observation),
  `assessment_timestamp_utc`.
- **Status:** `status` (`normal`, `attention_needed`, `rest_recommended` or
  `insufficient_data`), `status_label`, `explanation`, `suggested_action`, and
  `task_context` (`{key, label}`).
- **Evidence:** `supporting_observations` (`[{kind, text, record_ids}]`),
  `baseline_comparisons` (`sleep`, `schedule`, `reaction_time`,
  `time_since_awakening_hours`), `data_quality_issues`, `rule_checks`
  (`[{rule, value, threshold, triggered}]`), `evidence` (record IDs for the
  recent and baseline windows and cortisol), and `nights` (one entry per night,
  with role `baseline`, `recent` or `other`).
- **Context:** `is_synthetic_input`, `config`, `limitations`.

**Method.** All parameters are illustrative and configurable in `AnalysisConfig`.
1. Overlapping main-sleep records are grouped into nights. The representative
   record for a night is a wearable estimate with `good` or `fair` signal if
   there is one, otherwise a manual estimate. Poor or unusable signal, and time
   in bed alone, give no usable estimate.
2. The baseline is the earliest 5 usable nights (minimum 4) and the earliest 5
   completed assessments (minimum 3). The recent window is the latest 3 nights
   and the latest 2 assessments. The two windows never overlap.
3. Recent data must lie within 36 h of the evaluation point.
4. The rules:
   - Sleep is reduced when the recent mean is below 80 % of the baseline mean.
   - Reaction time is slowed when the recent median RT exceeds the baseline by
     more than 10 % or 2.5 SD, whichever is larger.
   - Lapses are up when there are at least 2 more per test than the baseline.
   - The schedule has shifted when mid-sleep moves by at least 2 h.
5. Classification:
   - **Rest Recommended:** reduced sleep *and* a performance decline.
   - **Attention Needed:** a performance decline alone, reduced sleep alone,
     or a schedule shift alone. The explanation states that a shift alone is
     not reduced concentration.
   - **Insufficient Data:** baselines or recent data are missing and no
     decline is evident.
   - **Normal:** none of the above.
6. Cortisol and naps are reported as context only.

## 8. Synthetic scenarios

`python data/generate_sleep_alertness.py` (seed `20261001`) writes fictional
records for four fictional crew members in `MISSION-SLEEP-DEMO-01`
(14 nights from 2026-10-01). The IDs do not overlap with the legacy dataset.

| Astronaut | Scenario | Content |
|---|---|---|
| AST-S01 | Stable sleep, stable alertness | ~8 h in bed, manual log + wearable each night, stages, stable RT, salivary cortisol every 3rd day |
| AST-S02 | Shifted schedule, no demonstrated decline | Bedtime shifts ~45 min/night to +6 h, duration maintained, RT stable |
| AST-S03 | Reduced sleep, slower reactions | Normal for 5 nights, then ~5 h in bed plus 3 naps; median RT and lapses increase; cortisol every 3rd day (no modeled relationship to alertness) |
| AST-S04 | Insufficient or missing data | 7 of 14 nights recorded; poor/unusable wearable signal; missing stages and estimates; 3 assessments, one incomplete; no cortisol |

All values are **fictional and not clinically validated**. Scenario effects
are illustrative, not a validated dose-response model. The legacy
720-measurement dataset is unchanged.

---

## 9. Actigraphy – `actigraphy_recordings` + `actigraphy_epochs`

| `actigraphy_recordings` field | Description |
|---|---|
| `recording_id` | `ACT-<hash>` (real) or `ACT-SYN-<astronaut>` (synthetic) |
| `astronaut_id`, `mission_id` | Scope. Real research data uses `RES-<code>` / `RESEARCH-ACTIWARE-EARTH` |
| `source_format` | `actiware_csv` or `astroguard_synthetic` |
| `source_name`, `source_subject_code` | File name and pseudonymous subject code only (no personal fields) |
| `device_model`, `epoch_seconds`, `start_utc`, `end_utc`, `epoch_count` | Recording metadata |
| `data_origin`, `is_synthetic` | `actiwatch_export` (0) or `synthetic` (1) |
| `attribution` | Source attribution (e.g. Zenodo 4961281, CC0) |

| `actigraphy_epochs` field | Description |
|---|---|
| `recording_id`, `epoch_start_utc` | PK |
| `activity_count` | Counts per epoch, nullable (`NaN` → NULL) |
| `white_light_lux` | Ambient white light, nullable |
| `off_wrist`, `event_marker` | 0/1, nullable |
| `device_sleep_wake` | `sleep`/`wake` as scored by Actiware, nullable |
| `interval_status` | `active`, `rest`, `rest_sleep`, `excluded`, nullable |

**Real input format.** "Actiware Export File (Version 05.00)", verified against
Zenodo 4961281. The parser reads the `Epoch-by-Epoch Data` section. Only Date,
Time and Activity are required. Time is converted to UTC with the header's
`Time Zone Offset`. Date order (day/month or month/day) is detected, and an
ambiguous file must pass `date_order` explicitly.

**Derived sleep periods.**
- A sleep period is each contiguous REST/REST-S interval of at least 30 min. If
  the file has no intervals, the heuristic's sleep bouts are used instead.
- Estimated sleep is the epochs scored as sleep (the device score if present).
- Sensor quality comes from the valid-epoch coverage: `good` ≥ 95 %, `fair`
  ≥ 80 %, `poor` ≥ 50 %, otherwise `unusable`, which gives no estimate.
- A period is `main` if it is at least 3 h, otherwise a `nap`.
- Periods are stored as `wearable` sleep sessions with `is_synthetic = 0`.

**Heuristic.** Weighted activity window (60-s epochs: 0.04, 0.2, 1, 0.2, 0.04)
compared with a wake threshold of 40 counts. It is Actiware-*style* and
illustrative: not the vendor implementation and not validated here.
