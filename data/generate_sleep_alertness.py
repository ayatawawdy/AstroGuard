"""Reproducible synthetic sleep, alertness and cortisol scenarios (Stage 1A).

Writes fictional demonstration records to ``data/synthetic/sleep_alertness/``:

* ``scenarios.json``             fictional crew, scenario descriptions, seed
* ``sleep_sessions.csv``         main sleep and naps (manual / wearable origin)
* ``sleep_stages.csv``           optional estimated stage intervals
* ``alertness_assessments.csv``  PVT-inspired assessment metadata
* ``reaction_time_trials.csv``   individual reaction-time trials
* ``cortisol_measurements.csv``  optional cortisol observations

Every record is synthetic (``is_synthetic = true``). Values are shaped to look
plausible for a demo but are NOT derived from, or validated against, real
astronaut or clinical data. Scenario effects (e.g. slower reaction times after
reduced sleep) are illustrative only and are not a validated dose-response
model. This generator does not touch the legacy 720-measurement dataset.

Run from the repository root::

    python data/generate_sleep_alertness.py
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

GENERATOR_VERSION = "1.0"
DEFAULT_SEED = 20261001
OUTPUT_DIR = Path(__file__).resolve().parent / "synthetic" / "sleep_alertness"

MISSION_ID = "MISSION-SLEEP-DEMO-01"
MISSION_NAME = "Fictional Sleep & Alertness Demo Mission"
FIRST_NIGHT = datetime(2026, 10, 1, tzinfo=timezone.utc)  # evening of night 0
NIGHTS = 14

PROTOCOL_VERSION = "astroguard-pvt-inspired-v0.1-synthetic"
TEST_DURATION_MS = 180_000         # 3-minute PVT-inspired session
RESPONSE_TIMEOUT_MS = 3_000        # synthetic "no response" window
LAPSE_THRESHOLD_MS = 500.0
FALSE_START_THRESHOLD_MS = 100.0

DISCLAIMER = (
    "Fictional synthetic data for software demonstration only. Not real "
    "astronaut data. Values, scenario effects and scoring parameters are not "
    "clinically validated and must not be used for medical decisions."
)

SCENARIOS = [
    {
        "astronaut_id": "AST-S01",
        "display_name": "Synthetic Crew S01",
        "scenario_key": "stable_sleep_stable_alertness",
        "description": "Regular main sleep (~8 h in bed) with stable reaction-time "
                       "performance. Manual log and wearable recording each night.",
    },
    {
        "astronaut_id": "AST-S02",
        "display_name": "Synthetic Crew S02",
        "scenario_key": "shifted_schedule_no_decline",
        "description": "Sleep window shifts ~45 min later per night for 8 nights "
                       "(+6 h), duration maintained; no demonstrated performance decline.",
    },
    {
        "astronaut_id": "AST-S03",
        "display_name": "Synthetic Crew S03",
        "scenario_key": "reduced_sleep_slower_reactions",
        "description": "Normal sleep for 5 nights, then ~5 h in bed with occasional "
                       "naps; reaction times slow and lapses increase (illustrative).",
    },
    {
        "astronaut_id": "AST-S04",
        "display_name": "Synthetic Crew S04",
        "scenario_key": "insufficient_or_missing_data",
        "description": "Sparse manual logs, poor/unusable wearable signal, missing "
                       "stages, missing nights and an incomplete assessment.",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def _night_start(night: int, clock_minutes: float) -> datetime:
    """Bedtime for ``night`` given minutes after 00:00 of that evening's date."""
    return _minute(FIRST_NIGHT + timedelta(days=night, minutes=float(clock_minutes)))


def _make_stages(rng, bedtime: datetime, wake: datetime, *, deep_scale=1.0,
                 wake_probability=0.3) -> list[tuple[str, datetime, datetime]]:
    """Fictional cycle-like stage sequence that exactly tiles [bedtime, wake)."""
    total = int((wake - bedtime).total_seconds() // 60)
    latency = int(rng.integers(5, 21)) if total > 60 else 4
    final_awake = int(rng.integers(3, 11)) if total > 60 else 2
    budget = total - latency - final_awake
    segments = [["awake", latency]]
    cycle = 0
    while budget > 0:
        length = int(rng.integers(80, 111))
        deep = max(0.03, 0.28 - 0.08 * cycle) * deep_scale
        rem = min(0.35, 0.12 + 0.07 * cycle)
        light = 1.0 - deep - rem
        parts = [("light", length * light * 0.6), ("deep", length * deep),
                 ("light", length * light * 0.4), ("rem", length * rem)]
        if rng.random() < wake_probability:
            parts.append(("awake", int(rng.integers(1, 6))))
        for stage, minutes in parts:
            minutes = min(int(round(minutes)), budget)
            if minutes <= 0:
                continue
            if segments[-1][0] == stage:
                segments[-1][1] += minutes
            else:
                segments.append([stage, minutes])
            budget -= minutes
        cycle += 1
    if segments[-1][0] == "awake":
        segments[-1][1] += final_awake
    else:
        segments.append(["awake", final_awake])

    stages, cursor = [], bedtime
    for stage, minutes in segments:
        end = cursor + timedelta(minutes=minutes)
        stages.append((stage, cursor, end))
        cursor = end
    assert cursor == wake, "stages must tile the session exactly"
    return stages


def _make_trials(rng, *, median_ms, lapse_probability, false_start_probability,
                 duration_ms=TEST_DURATION_MS) -> list[dict]:
    """Fictional PVT-inspired trials until the test duration is reached."""
    trials, elapsed, index = [], 0, 0
    while elapsed < duration_ms:
        index += 1
        isi = float(rng.integers(2_000, 10_001))
        draw = rng.random()
        early = False
        if draw < false_start_probability:
            if rng.random() < 0.5:
                early, rt = True, None
            else:
                rt = round(float(rng.uniform(40, 99)), 1)
        elif draw < false_start_probability + lapse_probability:
            rt = None if rng.random() < 0.2 else round(float(rng.uniform(500, 1_200)), 1)
        else:
            rt = round(float(median_ms * math.exp(rng.normal(0.0, 0.12))), 1)
        trials.append({
            "trial_index": index,
            "interstimulus_interval_ms": isi,
            "reaction_time_ms": rt,
            "responded_before_stimulus": early,
        })
        elapsed += isi + (RESPONSE_TIMEOUT_MS if rt is None and not early else (rt or 0))
    return trials


class _Collector:
    def __init__(self):
        self.sessions, self.stages = [], []
        self.assessments, self.trials, self.cortisol = [], [], []

    def session(self, astronaut, night_label, origin, bedtime, wake, *, sleep_type="main",
                estimate=None, quality=None, sensor_quality=None, coverage=None,
                missing_notes=None, notes=None, stages=None):
        code = astronaut.split("-")[1]
        suffix = {"manual": "M", "wearable": "W"}[origin]
        session_id = f"SLP-{code}-{night_label}-{'NAP-' if sleep_type == 'nap' else ''}{suffix}"
        if stages is not None:
            estimate = float(sum((e - s).total_seconds() / 60 for st, s, e in stages
                                 if st != "awake"))
            for stage, start, end in stages:
                self.stages.append({
                    "sleep_session_id": session_id, "stage_type": stage,
                    "start_utc": _utc(start), "end_utc": _utc(end),
                    "source": origin, "is_estimated": True, "is_synthetic": True,
                })
        self.sessions.append({
            "sleep_session_id": session_id, "astronaut_id": astronaut,
            "mission_id": MISSION_ID, "sleep_type": sleep_type,
            "bedtime_utc": _utc(bedtime), "wake_time_utc": _utc(wake),
            "estimated_sleep_minutes": estimate, "data_origin": origin,
            "is_synthetic": True, "self_reported_quality": quality,
            "sensor_quality": sensor_quality, "sensor_coverage_fraction": coverage,
            "missing_data_notes": missing_notes,
            "notes": notes or "Synthetic fictional record",
        })
        return session_id

    def assessment(self, astronaut, day, when, trials, *, fatigue=None,
                   completion="completed", notes=None):
        assessment_id = f"ALR-{astronaut.split('-')[1]}-D{day:02d}"
        self.assessments.append({
            "assessment_id": assessment_id, "astronaut_id": astronaut,
            "mission_id": MISSION_ID, "assessment_utc": _utc(_minute(when)),
            "protocol_version": PROTOCOL_VERSION, "timing_method": "synthetic_generator",
            "completion_status": completion, "data_origin": "synthetic",
            "is_synthetic": True, "lapse_threshold_ms": LAPSE_THRESHOLD_MS,
            "false_start_threshold_ms": FALSE_START_THRESHOLD_MS,
            "self_reported_fatigue": fatigue,
            "notes": notes or "Synthetic PVT-inspired assessment",
        })
        for trial in trials:
            self.trials.append({"assessment_id": assessment_id, **trial})

    def cortisol_sample(self, astronaut, day, session_id, wake, minutes_after, value):
        self.cortisol.append({
            "cortisol_id": f"CRT-{astronaut.split('-')[1]}-D{day:02d}",
            "astronaut_id": astronaut, "mission_id": MISSION_ID,
            "sample_utc": _utc(wake + timedelta(minutes=minutes_after)),
            "value": value, "unit": "nmol/L", "sampling_method": "saliva",
            "minutes_since_awakening": float(minutes_after),
            "sleep_session_id": session_id, "data_origin": "synthetic",
            "is_synthetic": True,
            "notes": "Synthetic salivary cortisol (context only; not an alertness indicator)",
        })


def _manual_log(rng, out, astronaut, night, bedtime, wake, true_sleep, quality):
    """Manual self-report: rounded, slightly misremembered times and estimate."""
    reported_bed = _minute(bedtime + timedelta(minutes=int(rng.integers(-10, 11))))
    reported_bed -= timedelta(minutes=reported_bed.minute % 5)
    reported_wake = _minute(wake + timedelta(minutes=int(rng.integers(-5, 11))))
    reported_wake -= timedelta(minutes=reported_wake.minute % 5)
    tib = (reported_wake - reported_bed).total_seconds() / 60
    estimate = None
    if true_sleep is not None:
        estimate = float(min(tib, 15 * round((true_sleep + rng.normal(0, 20)) / 15)))
    out.session(astronaut, f"N{night:02d}", "manual", reported_bed, reported_wake,
                estimate=estimate, quality=quality,
                notes="Synthetic manual sleep log (self-reported)")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def _stable(rng, out, astronaut):
    for night in range(NIGHTS):
        bedtime = _night_start(night, 22 * 60 + 30 + rng.normal(0, 12))
        wake = _minute(bedtime + timedelta(minutes=float(480 + rng.normal(0, 15))))
        stages = _make_stages(rng, bedtime, wake)
        sid = out.session(astronaut, f"N{night:02d}", "wearable", bedtime, wake,
                          sensor_quality="good",
                          coverage=round(float(rng.uniform(0.93, 0.99)), 3), stages=stages)
        sleep = out.sessions[-1]["estimated_sleep_minutes"]
        _manual_log(rng, out, astronaut, night, bedtime, wake, sleep, int(rng.integers(3, 6)))
        trials = _make_trials(rng, median_ms=255 + rng.normal(0, 6),
                              lapse_probability=0.01, false_start_probability=0.01)
        out.assessment(astronaut, night, wake + timedelta(minutes=60 + int(rng.integers(0, 20))),
                       trials, fatigue=int(rng.integers(2, 4)))
        if night % 3 == 0:
            out.cortisol_sample(astronaut, night, sid, wake, 30,
                                round(float(max(3.0, rng.normal(14, 3))), 1))


def _shifted(rng, out, astronaut):
    for night in range(NIGHTS):
        shift = min(night, 8) * 45
        bedtime = _night_start(night, 22 * 60 + 30 + shift + rng.normal(0, 10))
        wake = _minute(bedtime + timedelta(minutes=float(470 + rng.normal(0, 15))))
        stages = _make_stages(rng, bedtime, wake)
        sleep = sum((e - s).total_seconds() / 60 for st, s, e in stages if st != "awake")
        out.session(astronaut, f"N{night:02d}", "wearable", bedtime, wake,
                    sensor_quality="good" if night % 5 else "fair",
                    coverage=round(float(rng.uniform(0.88, 0.98)), 3), stages=stages,
                    notes=f"Synthetic record; fictional scheduled sleep shift +{shift} min")
        if night % 3 == 0:
            _manual_log(rng, out, astronaut, night, bedtime, wake, sleep, int(rng.integers(3, 5)))
        trials = _make_trials(rng, median_ms=262 + rng.normal(0, 6),
                              lapse_probability=0.012, false_start_probability=0.01)
        out.assessment(astronaut, night, wake + timedelta(minutes=60 + int(rng.integers(0, 20))),
                       trials, fatigue=int(rng.integers(2, 4)))


def _reduced(rng, out, astronaut):
    for night in range(NIGHTS):
        restricted = night >= 5
        if restricted:
            bedtime = _night_start(night, 25 * 60 + rng.normal(0, 10))   # ~01:00 next day
            minutes_in_bed = 300 + rng.normal(0, 15)
        else:
            bedtime = _night_start(night, 22 * 60 + 45 + rng.normal(0, 10))
            minutes_in_bed = 465 + rng.normal(0, 15)
        wake = _minute(bedtime + timedelta(minutes=float(minutes_in_bed)))
        stages = _make_stages(rng, bedtime, wake, deep_scale=1.1 if restricted else 1.0,
                              wake_probability=0.2)
        sid = out.session(astronaut, f"N{night:02d}", "wearable", bedtime, wake,
                          sensor_quality="good",
                          coverage=round(float(rng.uniform(0.92, 0.99)), 3), stages=stages)
        sleep = out.sessions[-1]["estimated_sleep_minutes"]
        if night % 2 == 0:
            _manual_log(rng, out, astronaut, night, bedtime, wake, sleep,
                        int(rng.integers(1, 3)) if restricted else int(rng.integers(3, 5)))

        nights_restricted = max(0, night - 4)
        trials = _make_trials(
            rng, median_ms=258 + 6 * nights_restricted + rng.normal(0, 6),
            lapse_probability=min(0.15, 0.01 + 0.012 * nights_restricted),
            false_start_probability=0.01 + 0.002 * nights_restricted,
        )
        fatigue = min(7, 2 + nights_restricted // 2 + int(rng.integers(0, 2)))
        out.assessment(astronaut, night, wake + timedelta(minutes=45 + int(rng.integers(0, 20))),
                       trials, fatigue=fatigue)

        if restricted and night in (7, 9, 11):
            nap_start = _minute(wake.replace(hour=13, minute=30)
                                + timedelta(minutes=int(rng.integers(0, 15))))
            nap_end = nap_start + timedelta(minutes=int(rng.integers(25, 41)))
            out.session(astronaut, f"D{night:02d}", "wearable", nap_start, nap_end,
                        sleep_type="nap", sensor_quality="good",
                        coverage=round(float(rng.uniform(0.9, 0.99)), 3),
                        stages=_make_stages(rng, nap_start, nap_end, wake_probability=0.0),
                        notes="Synthetic daytime nap")
        if night % 3 == 0:
            out.cortisol_sample(astronaut, night, sid, wake, 30,
                                round(float(max(3.0, rng.normal(14, 3))), 1))


def _missing(rng, out, astronaut):
    # One fixed (fictional) bed/wake time per night, whether or not it is recorded.
    times = {}
    for night in range(NIGHTS):
        bedtime = _night_start(night, 23 * 60 + rng.normal(0, 20))
        times[night] = (bedtime,
                        _minute(bedtime + timedelta(minutes=float(450 + rng.normal(0, 25)))))
    bed_wake = times.__getitem__

    for night in (0, 1, 10):
        bedtime, wake = bed_wake(night)
        _manual_log(rng, out, astronaut, night, bedtime, wake, 400, int(rng.integers(2, 5)))
    # Wearable with a degraded signal: estimate kept, no stage data.
    bedtime, wake = bed_wake(2)
    out.session(astronaut, "N02", "wearable", bedtime, wake, sensor_quality="poor",
                coverage=0.55, estimate=float(15 * round(rng.normal(390, 20) / 15)),
                missing_notes="Intermittent sensor contact; stage estimates unavailable")
    # Wearable with an unusable signal: no sleep estimate, no stages.
    bedtime, wake = bed_wake(4)
    out.session(astronaut, "N04", "wearable", bedtime, wake, sensor_quality="unusable",
                coverage=0.08,
                missing_notes="Sensor signal insufficient; sleep could not be estimated")
    # Manual log with only bed/wake times (no estimate, no quality rating).
    bedtime, wake = bed_wake(5)
    _manual_log(rng, out, astronaut, 5, bedtime, wake, None, None)
    out.sessions[-1]["missing_data_notes"] = "No sleep estimate or quality rating reported"
    # Wearable with fair signal and stages, but partial coverage.
    bedtime, wake = bed_wake(12)
    out.session(astronaut, "N12", "wearable", bedtime, wake, sensor_quality="fair",
                coverage=0.81, stages=_make_stages(rng, bedtime, wake),
                missing_notes="Coverage below full night")
    # Nights 3, 6-9, 11 and 13 have no sleep record at all.

    for day in (0, 5):
        _, wake = bed_wake(day)
        trials = _make_trials(rng, median_ms=265 + rng.normal(0, 8),
                              lapse_probability=0.02, false_start_probability=0.01)
        out.assessment(astronaut, day, wake + timedelta(minutes=70), trials,
                       fatigue=None if day == 5 else int(rng.integers(2, 5)))
    _, wake = bed_wake(11)
    trials = _make_trials(rng, median_ms=265, lapse_probability=0.02,
                          false_start_probability=0.01, duration_ms=25_000)
    out.assessment(astronaut, 11, wake + timedelta(minutes=70), trials,
                   completion="incomplete",
                   notes="Synthetic assessment interrupted before the planned duration")


_BUILDERS = {
    "stable_sleep_stable_alertness": _stable,
    "shifted_schedule_no_decline": _shifted,
    "reduced_sleep_slower_reactions": _reduced,
    "insufficient_or_missing_data": _missing,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(seed: int = DEFAULT_SEED) -> dict[str, pd.DataFrame]:
    """Build all scenario tables in memory. Same seed -> identical output."""
    out = _Collector()
    for index, scenario in enumerate(SCENARIOS):
        rng = np.random.default_rng([seed, index])   # independent per scenario
        _BUILDERS[scenario["scenario_key"]](rng, out, scenario["astronaut_id"])

    frames = {
        "sleep_sessions": pd.DataFrame(out.sessions),
        "sleep_stages": pd.DataFrame(out.stages),
        "alertness_assessments": pd.DataFrame(out.assessments),
        "reaction_time_trials": pd.DataFrame(out.trials),
        "cortisol_measurements": pd.DataFrame(out.cortisol),
    }
    frames["sleep_sessions"] = frames["sleep_sessions"].sort_values(
        ["astronaut_id", "bedtime_utc", "data_origin"], ignore_index=True)
    frames["sleep_stages"] = frames["sleep_stages"].sort_values(
        ["sleep_session_id", "start_utc"], ignore_index=True)
    frames["alertness_assessments"] = frames["alertness_assessments"].sort_values(
        ["astronaut_id", "assessment_utc"], ignore_index=True)
    frames["reaction_time_trials"] = frames["reaction_time_trials"].sort_values(
        ["assessment_id", "trial_index"], ignore_index=True)
    # Integer-valued optional columns: keep as nullable integers in CSV output.
    for table, column in (("sleep_sessions", "self_reported_quality"),
                          ("alertness_assessments", "self_reported_fatigue")):
        frames[table][column] = frames[table][column].astype("Int64")
    return frames


def manifest(seed: int = DEFAULT_SEED) -> dict:
    return {
        "dataset": "astroguard-sleep-alertness-synthetic",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "is_synthetic": True,
        "disclaimer": DISCLAIMER,
        "mission": {"mission_id": MISSION_ID, "mission_name": MISSION_NAME},
        "protocol": {
            "protocol_version": PROTOCOL_VERSION,
            "test_duration_ms": TEST_DURATION_MS,
            "lapse_threshold_ms": LAPSE_THRESHOLD_MS,
            "false_start_threshold_ms": FALSE_START_THRESHOLD_MS,
            "note": "Scoring parameters label trials only; they are not alert thresholds.",
        },
        "astronauts": SCENARIOS,
    }


def write_dataset(output_dir: Path | str = OUTPUT_DIR, seed: int = DEFAULT_SEED) -> dict[str, int]:
    """Generate and write the dataset. Returns row counts per file."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, frame in generate(seed).items():
        frame.to_csv(output_dir / f"{name}.csv", index=False, encoding="utf-8",
                     lineterminator="\n")
        counts[name] = len(frame)
    (output_dir / "scenarios.json").write_text(
        json.dumps(manifest(seed), indent=2) + "\n", encoding="utf-8", newline="\n")
    return counts


if __name__ == "__main__":
    for table, count in write_dataset().items():
        print(f"{table}: {count} synthetic rows")
    print(f"Saved to: {OUTPUT_DIR}")
    print(DISCLAIMER)
