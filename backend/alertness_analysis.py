"""Personalized sleep & alertness analysis (transparent, illustrative heuristics).

``analyze_alertness(astronaut_id, mission_id)`` reads stored sleep sessions,
reaction-time assessments and (optionally) cortisol for ONE astronaut and ONE
mission, compares the most recent observations with that astronaut's own
earlier baseline, and returns one of four statuses:

    normal | attention_needed | rest_recommended | insufficient_data

Design rules (see docs/contracts.md, section 8):

* Personal baselines use only observations EARLIER than the recent window
  (the earliest ``baseline_*`` usable records). The recent observations being
  evaluated never enter their own baseline. Astronauts are never compared with
  each other.
* Time in bed is not sleep: only ``estimated_sleep_minutes`` counts as sleep.
* A shifted sleep schedule alone never implies reduced alertness.
* Cortisol is reported as context only and never changes the status.
* Every threshold below is an ILLUSTRATIVE, configurable heuristic for a
  hackathon prototype. None is clinically validated. The output is decision
  support for humans; it never stops, pauses or blocks any task.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from backend import sleep_alertness as sa

ANALYSIS_VERSION = "astroguard-alertness-heuristic-0.1"

STATUS_LABELS = {
    "normal": "Normal",
    "attention_needed": "Attention Needed",
    "rest_recommended": "Rest Recommended",
    "insufficient_data": "Insufficient Data",
}

TASK_CONTEXTS = {
    "routine_monitoring": "Routine Monitoring",
    "scientific_experiment": "Scientific Experiment",
    "equipment_maintenance": "Equipment Maintenance",
    "safety_critical_operation": "Safety-Critical Operation",
}

LIMITATIONS = [
    "Prototype decision support only: not a medical diagnosis or a validated fatigue detector.",
    "All thresholds are illustrative and configurable; none is clinically validated.",
    "Sleep durations are estimates (self-report or wrist actigraphy), not polysomnography.",
    "Recommendations are advisory. AstroGuard never pauses or terminates any task; "
    "human operators and mission procedures retain authority.",
]


@dataclass(frozen=True)
class AnalysisConfig:
    """Illustrative, configurable heuristic parameters (NOT clinical thresholds)."""

    baseline_nights: int = 5             # earliest usable nights forming the sleep baseline
    min_baseline_nights: int = 4
    recent_nights: int = 3               # latest usable nights being evaluated
    baseline_assessments: int = 5
    min_baseline_assessments: int = 3
    recent_assessments: int = 2
    recency_hours: float = 36.0          # recent data must be this close to the evaluation point
    sleep_reduction_ratio: float = 0.80  # recent mean sleep < 80 % of baseline -> "reduced"
    rt_slowing_ratio: float = 0.10       # recent median RT > baseline by 10 % ...
    rt_slowing_sd: float = 2.5           # ... or by 2.5 baseline SDs, whichever is larger
    lapse_increase: float = 2.0          # recent mean lapses/test exceed baseline by >= 2
    schedule_shift_hours: float = 2.0    # mid-sleep shift that counts as a schedule change
    usable_sensor_qualities: tuple = ("good", "fair")


DEFAULT_CONFIG = AnalysisConfig()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(text: str) -> datetime:
    return datetime.strptime(text, sa.UTC_FORMAT).replace(tzinfo=timezone.utc)


def _fmt(moment: datetime | None) -> str | None:
    return None if moment is None else moment.strftime(sa.UTC_FORMAT)


def _hours(minutes: float | None) -> str:
    return "n/a" if minutes is None else f"{minutes / 60:.1f} h"


def _clock_hours(moment: datetime) -> float:
    return moment.hour + moment.minute / 60 + moment.second / 3600


def _circular_mean_hours(values: list[float]) -> float:
    angles = [v / 24 * 2 * math.pi for v in values]
    x = sum(math.cos(a) for a in angles) / len(angles)
    y = sum(math.sin(a) for a in angles) / len(angles)
    return (math.atan2(y, x) / (2 * math.pi) * 24) % 24


def _signed_hour_diff(a: float, b: float) -> float:
    """a - b on a 24 h clock, in (-12, 12]."""
    diff = (a - b) % 24
    return diff - 24 if diff > 12 else diff


def _spread_hours(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    centre = _circular_mean_hours(values)
    return statistics.stdev(_signed_hour_diff(v, centre) for v in values)


def _session_rank(session: dict, config: AnalysisConfig) -> tuple:
    """Lower is better when several records describe the same sleep period."""
    usable_wearable = (session["data_origin"] in ("wearable", "synthetic")
                       and session["estimated_sleep_minutes"] is not None
                       and (session["sensor_quality"] in config.usable_sensor_qualities
                            or (session["data_origin"] == "synthetic"
                                and session["sensor_quality"] is None)))
    usable_manual = (session["data_origin"] == "manual"
                     and session["estimated_sleep_minutes"] is not None)
    return (0 if usable_wearable else 1 if usable_manual else 2, session["bedtime_utc"])


def build_nights(sessions: list[dict], config: AnalysisConfig = DEFAULT_CONFIG) -> list[dict]:
    """Group overlapping MAIN-sleep records (e.g. manual log + wearable) into nights.

    Each night uses one representative record: a wearable estimate with usable
    signal quality is preferred, then a manual estimate. A night whose records
    carry no usable sleep estimate is kept with ``usable = False``.
    """
    main = sorted((s for s in sessions if s["sleep_type"] == "main"),
                  key=lambda s: s["bedtime_utc"])
    clusters: list[list[dict]] = []
    for session in main:
        if clusters and session["bedtime_utc"] < max(s["wake_time_utc"] for s in clusters[-1]):
            clusters[-1].append(session)
        else:
            clusters.append([session])

    nights = []
    for cluster in clusters:
        best = min(cluster, key=lambda s: _session_rank(s, config))
        usable = _session_rank(best, config)[0] < 2
        bed, wake = _parse(best["bedtime_utc"]), _parse(best["wake_time_utc"])
        reasons = []
        if not usable:
            qualities = sorted({s["sensor_quality"] for s in cluster if s["sensor_quality"]})
            reasons.append("no usable sleep estimate"
                           + (f" (sensor quality: {', '.join(qualities)})" if qualities else ""))
        nights.append({
            "night_id": best["sleep_session_id"],
            "session_ids": [s["sleep_session_id"] for s in cluster],
            "bedtime_utc": best["bedtime_utc"],
            "wake_time_utc": best["wake_time_utc"],
            "time_in_bed_minutes": best["time_in_bed_minutes"],
            "estimated_sleep_minutes": best["estimated_sleep_minutes"] if usable else None,
            "data_origin": best["data_origin"],
            "sensor_quality": best["sensor_quality"],
            "is_synthetic": any(s["is_synthetic"] for s in cluster),
            "mid_sleep_clock_h": _clock_hours(bed + (wake - bed) / 2),
            "usable": usable,
            "issues": reasons,
        })
    return nights


def _split(items: list, size: int, minimum: int, recent: int):
    """Return (baseline, recent) using earliest items for the baseline."""
    if len(items) < minimum + 1:
        return None, None
    recent_count = min(recent, len(items) - minimum)
    recent_items = items[-recent_count:]
    pool = items[:-recent_count]
    return pool[:size], recent_items


# ---------------------------------------------------------------------------
# Recommendation wording
# ---------------------------------------------------------------------------

def suggested_action(status: str, task_context: str) -> str:
    """Advisory wording tailored to the task. Never instructs automatic interruption."""
    task = TASK_CONTEXTS[task_context]
    safety = task_context == "safety_critical_operation"
    base = {
        "normal": "Continue routine monitoring and keep logging sleep and scheduled "
                  "reaction-time assessments.",
        "attention_needed": "Complete another reaction-time assessment and keep monitoring sleep.",
        "rest_recommended": "Consider a rest break according to mission procedures, then repeat "
                            "the reaction-time assessment.",
        "insufficient_data": "Collect the missing data listed below. AstroGuard draws no "
                             "alertness conclusion from the available records.",
    }[status]
    by_task = {
        ("attention_needed", "scientific_experiment"):
            "Consider scheduling the most demanding experiment steps after a repeat assessment.",
        ("attention_needed", "equipment_maintenance"):
            "Consider reviewing the task schedule and using standard cross-check procedures.",
        ("rest_recommended", "routine_monitoring"):
            "Consider a rest break before resuming demanding work.",
        ("rest_recommended", "scientific_experiment"):
            "If the schedule allows, consider postponing non-time-critical experiment steps "
            "until after a rest break.",
        ("rest_recommended", "equipment_maintenance"):
            "Review the maintenance schedule with the team and consider a rest break before "
            "complex steps, per procedures.",
    }
    parts = [base]
    if (status, task_context) in by_task:
        parts.append(by_task[status, task_context])
    if safety:
        if status in ("attention_needed", "rest_recommended"):
            parts.append("For this safety-critical operation, inform the responsible operator "
                         "and consult Mission Control and mission procedures.")
        parts.append("AstroGuard does not pause or terminate the operation; human operators "
                     "and mission procedures take priority.")
    return f"[{task}] " + " ".join(parts)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_alertness(astronaut_id: str, mission_id: str, *,
                      task_context: str = "routine_monitoring",
                      config: AnalysisConfig = DEFAULT_CONFIG, synthetic_only: bool = False,
                      db_path=None) -> dict:
    """Evaluate the latest stored observations against the personal baseline.

    The evaluation point is the latest stored observation (sleep wake time or
    assessment time) for this astronaut and mission, so historical or demo
    data is evaluated at the time it describes, not "now". See module docs.

    ``synthetic_only=True`` restricts the INPUT to records labeled synthetic
    (used for the predefined fictional demo scenarios, so a real interactive
    entry stored on a demo profile cannot change the scenario). The algorithm
    itself is identical; excluded record IDs are reported in
    ``excluded_record_ids``.
    """
    if task_context not in TASK_CONTEXTS:
        raise ValueError(f"task_context must be one of {', '.join(TASK_CONTEXTS)}")

    sessions = sa.get_sleep_sessions(astronaut_id, mission_id=mission_id, db_path=db_path)
    assessments = sa.get_alertness_assessments(astronaut_id, mission_id=mission_id,
                                               db_path=db_path)
    cortisol = sa.get_cortisol_measurements(astronaut_id, mission_id=mission_id, db_path=db_path)
    excluded: list[str] = []
    if synthetic_only:
        excluded = ([s["sleep_session_id"] for s in sessions if not s["is_synthetic"]]
                    + [a["assessment_id"] for a in assessments if not a["is_synthetic"]]
                    + [c["cortisol_id"] for c in cortisol if not c["is_synthetic"]])
        sessions = [s for s in sessions if s["is_synthetic"]]
        assessments = [a for a in assessments if a["is_synthetic"]]
        cortisol = [c for c in cortisol if c["is_synthetic"]]

    issues: list[str] = []
    observations: list[dict] = []
    checks: list[dict] = []
    comparisons: dict = {}
    evidence = {"recent_sleep_session_ids": [], "baseline_sleep_session_ids": [],
                "recent_assessment_ids": [], "baseline_assessment_ids": [],
                "cortisol_ids": []}

    times = [_parse(s["wake_time_utc"]) for s in sessions]
    times += [_parse(a["assessment_utc"]) for a in assessments]
    evaluated_at = max(times) if times else None
    recency_limit = (evaluated_at - timedelta(hours=config.recency_hours)
                     if evaluated_at else None)

    # ---------------- Sleep ----------------
    nights = build_nights(sessions, config)
    usable_nights = [n for n in nights if n["usable"]]
    for night in nights:
        if not night["usable"]:
            issues.append(f"Night of {night['bedtime_utc'][:10]}: " + "; ".join(night["issues"]))
    naps = [s for s in sessions if s["sleep_type"] == "nap"]

    base_nights, recent_nights = _split(usable_nights, config.baseline_nights,
                                        config.min_baseline_nights, config.recent_nights)
    sleep_baseline_ok = base_nights is not None
    recent_sleep_ok = False
    sleep_reduced = schedule_shift = False
    if not sleep_baseline_ok:
        issues.append(f"Only {len(usable_nights)} night(s) with a usable sleep estimate; at least "
                      f"{config.min_baseline_nights + 1} are needed for a personal sleep baseline "
                      f"plus a recent night.")
    else:
        latest_wake = _parse(recent_nights[-1]["wake_time_utc"])
        recent_sleep_ok = latest_wake >= recency_limit
        if not recent_sleep_ok:
            issues.append(f"No usable sleep estimate within {config.recency_hours:.0f} h before "
                          f"the evaluation point (latest usable night ended "
                          f"{recent_nights[-1]['wake_time_utc']}).")
        base_sleep = statistics.fmean(n["estimated_sleep_minutes"] for n in base_nights)
        recent_sleep = statistics.fmean(n["estimated_sleep_minutes"] for n in recent_nights)
        base_bed = statistics.fmean(n["time_in_bed_minutes"] for n in base_nights)
        recent_bed = statistics.fmean(n["time_in_bed_minutes"] for n in recent_nights)
        ratio = recent_sleep / base_sleep if base_sleep else None
        sleep_reduced = recent_sleep_ok and ratio is not None and ratio < config.sleep_reduction_ratio
        base_mid = _circular_mean_hours([n["mid_sleep_clock_h"] for n in base_nights])
        recent_mid = _circular_mean_hours([n["mid_sleep_clock_h"] for n in recent_nights])
        shift = _signed_hour_diff(recent_mid, base_mid)
        schedule_shift = recent_sleep_ok and abs(shift) >= config.schedule_shift_hours
        evidence["recent_sleep_session_ids"] = [i for n in recent_nights for i in n["session_ids"]]
        evidence["baseline_sleep_session_ids"] = [i for n in base_nights for i in n["session_ids"]]
        comparisons["sleep"] = {
            "baseline_nights": len(base_nights),
            "baseline_period": [base_nights[0]["bedtime_utc"], base_nights[-1]["wake_time_utc"]],
            "baseline_mean_sleep_minutes": round(base_sleep, 1),
            "recent_nights": len(recent_nights),
            "recent_mean_sleep_minutes": round(recent_sleep, 1),
            "last_night_sleep_minutes": recent_nights[-1]["estimated_sleep_minutes"],
            "ratio_to_baseline": None if ratio is None else round(ratio, 3),
            "baseline_mean_time_in_bed_minutes": round(base_bed, 1),
            "recent_mean_time_in_bed_minutes": round(recent_bed, 1),
        }
        comparisons["schedule"] = {
            "baseline_mid_sleep_utc_h": round(base_mid, 2),
            "recent_mid_sleep_utc_h": round(recent_mid, 2),
            "mid_sleep_shift_hours": round(shift, 2),
            "baseline_mid_sleep_spread_hours": _round(_spread_hours(
                [n["mid_sleep_clock_h"] for n in base_nights])),
            "recent_mid_sleep_spread_hours": _round(_spread_hours(
                [n["mid_sleep_clock_h"] for n in recent_nights])),
        }
        checks.append({"rule": "recent mean estimated sleep < "
                               f"{config.sleep_reduction_ratio:.0%} of personal baseline",
                       "value": comparisons["sleep"]["ratio_to_baseline"],
                       "threshold": config.sleep_reduction_ratio, "triggered": sleep_reduced})
        checks.append({"rule": f"|mid-sleep shift| >= {config.schedule_shift_hours:g} h",
                       "value": round(shift, 2), "threshold": config.schedule_shift_hours,
                       "triggered": schedule_shift})
        observations.append({
            "kind": "sleep",
            "text": f"Recent estimated sleep {_hours(recent_sleep)} per night "
                    f"({len(recent_nights)} night(s)) vs personal baseline {_hours(base_sleep)} "
                    f"({ratio:.0%} of baseline). Time in bed {_hours(recent_bed)} vs "
                    f"{_hours(base_bed)}.",
            "record_ids": evidence["recent_sleep_session_ids"],
        })
        observations.append({
            "kind": "schedule",
            "text": f"Mid-sleep time shifted {shift:+.1f} h relative to the baseline period"
                    + (" (schedule change)." if abs(shift) >= config.schedule_shift_hours
                       else " (within the usual range)."),
            "record_ids": evidence["recent_sleep_session_ids"],
        })
        recent_start = _parse(recent_nights[0]["bedtime_utc"])
        recent_naps = [s for s in naps if _parse(s["bedtime_utc"]) >= recent_start]
        if recent_naps:
            nap_minutes = sum(s["estimated_sleep_minutes"] or 0 for s in recent_naps)
            observations.append({
                "kind": "naps",
                "text": f"{len(recent_naps)} nap(s) in the recent period, about {nap_minutes:.0f} "
                        "min estimated sleep in total (reported separately from main sleep).",
                "record_ids": [s["sleep_session_id"] for s in recent_naps],
            })

    # ---------------- Reaction time ----------------
    completed = [a for a in assessments if a["completion_status"] == "completed"]
    incomplete = [a for a in assessments if a["completion_status"] != "completed"]
    for a in incomplete:
        issues.append(f"Assessment {a['assessment_id']} at {a['assessment_utc']} is incomplete and "
                      "was not used.")
    base_tests, recent_tests = _split(completed, config.baseline_assessments,
                                      config.min_baseline_assessments, config.recent_assessments)
    rt_baseline_ok = base_tests is not None
    recent_rt_ok = rt_slowed = lapses_up = False
    latest_assessment = completed[-1] if completed else None
    if not rt_baseline_ok:
        issues.append(f"Only {len(completed)} completed reaction-time assessment(s); at least "
                      f"{config.min_baseline_assessments + 1} are needed for a personal baseline "
                      "plus a recent assessment.")
    else:
        recent_rt_ok = _parse(recent_tests[-1]["assessment_utc"]) >= recency_limit
        if not recent_rt_ok:
            issues.append(f"No completed reaction-time assessment within "
                          f"{config.recency_hours:.0f} h before the evaluation point.")
        medians = [a["median_rt_ms"] for a in base_tests if a["median_rt_ms"] is not None]
        recent_medians = [a["median_rt_ms"] for a in recent_tests if a["median_rt_ms"] is not None]
        if not medians or not recent_medians:
            rt_baseline_ok = False
            issues.append("Assessments contain no valid responses for reaction-time statistics.")
        else:
            base_rt = statistics.fmean(medians)
            base_sd = statistics.stdev(medians) if len(medians) >= 2 else 0.0
            recent_rt = statistics.fmean(recent_medians)
            margin = max(config.rt_slowing_sd * base_sd, config.rt_slowing_ratio * base_rt)
            rt_slowed = recent_rt_ok and recent_rt > base_rt + margin
            base_lapses = statistics.fmean(a["lapse_count"] for a in base_tests)
            recent_lapses = statistics.fmean(a["lapse_count"] for a in recent_tests)
            lapses_up = recent_rt_ok and recent_lapses - base_lapses >= config.lapse_increase
            base_fs = statistics.fmean(a["false_start_count"] for a in base_tests)
            recent_fs = statistics.fmean(a["false_start_count"] for a in recent_tests)
            evidence["recent_assessment_ids"] = [a["assessment_id"] for a in recent_tests]
            evidence["baseline_assessment_ids"] = [a["assessment_id"] for a in base_tests]
            comparisons["reaction_time"] = {
                "baseline_assessments": len(base_tests),
                "baseline_mean_median_rt_ms": round(base_rt, 1),
                "baseline_sd_median_rt_ms": round(base_sd, 1),
                "slowing_margin_ms": round(margin, 1),
                "recent_assessments": len(recent_tests),
                "recent_mean_median_rt_ms": round(recent_rt, 1),
                "change_ms": round(recent_rt - base_rt, 1),
                "change_pct": round((recent_rt - base_rt) / base_rt * 100, 1),
                "baseline_mean_lapses": round(base_lapses, 2),
                "recent_mean_lapses": round(recent_lapses, 2),
                "baseline_mean_false_starts": round(base_fs, 2),
                "recent_mean_false_starts": round(recent_fs, 2),
            }
            checks.append({"rule": "recent median RT > baseline + max("
                                   f"{config.rt_slowing_sd:g} SD, {config.rt_slowing_ratio:.0%})",
                           "value": round(recent_rt, 1),
                           "threshold": round(base_rt + margin, 1), "triggered": rt_slowed})
            checks.append({"rule": f"recent mean lapses exceed baseline by >= "
                                   f"{config.lapse_increase:g}",
                           "value": round(recent_lapses, 2),
                           "threshold": round(base_lapses + config.lapse_increase, 2),
                           "triggered": lapses_up})
            observations.append({
                "kind": "reaction_time",
                "text": f"Recent median reaction time {recent_rt:.0f} ms vs personal baseline "
                        f"{base_rt:.0f} ms ({recent_rt - base_rt:+.0f} ms, "
                        f"{(recent_rt - base_rt) / base_rt:+.0%}); lapses per test "
                        f"{recent_lapses:.1f} vs {base_lapses:.1f}; false starts "
                        f"{recent_fs:.1f} vs {base_fs:.1f}.",
                "record_ids": evidence["recent_assessment_ids"],
            })

    # Time since awakening at the latest assessment (context).
    if latest_assessment is not None:
        moment = _parse(latest_assessment["assessment_utc"])
        wakes = [_parse(s["wake_time_utc"]) for s in sessions
                 if _parse(s["wake_time_utc"]) <= moment]
        if wakes:
            hours_awake = (moment - max(wakes)).total_seconds() / 3600
            comparisons["time_since_awakening_hours"] = round(hours_awake, 1)
            observations.append({"kind": "context",
                                 "text": f"Latest assessment taken {hours_awake:.1f} h after the "
                                         "most recent recorded wake time.",
                                 "record_ids": [latest_assessment["assessment_id"]]})

    # ---------------- Cortisol (context only) ----------------
    if cortisol and recency_limit is not None:
        recent_cortisol = [c for c in cortisol if _parse(c["sample_utc"]) >= recency_limit]
        if recent_cortisol:
            c = recent_cortisol[-1]
            evidence["cortisol_ids"] = [c["cortisol_id"]]
            since = ("" if c["minutes_since_awakening"] is None
                     else f", {c['minutes_since_awakening']:.0f} min after waking")
            observations.append({
                "kind": "cortisol",
                "text": f"Cortisol {c['value']:g} {c['unit']} ({c['sampling_method']}{since}). "
                        "Context only; not used to determine the status.",
                "record_ids": [c["cortisol_id"]],
            })

    # ---------------- Classification ----------------
    complete = sleep_baseline_ok and recent_sleep_ok and rt_baseline_ok and recent_rt_ok
    performance_decline = rt_slowed or lapses_up
    if performance_decline and sleep_reduced:
        status = "rest_recommended"
        explanation = ("Recent estimated sleep is below this astronaut's personal baseline and the "
                       "latest reaction-time assessments show "
                       + _decline_text(rt_slowed, lapses_up)
                       + " compared with their own earlier performance. Together these suggest a "
                         "possible reduction in alertness.")
    elif performance_decline:
        status = "attention_needed"
        explanation = ("Reaction-time performance shows " + _decline_text(rt_slowed, lapses_up)
                       + " compared with the personal baseline, without reduced estimated sleep"
                       + ("" if complete else " being demonstrated (sleep data incomplete)")
                       + ". A repeat assessment can show whether this persists.")
    elif not complete:
        if sleep_reduced:
            status = "attention_needed"
            explanation = ("Recent estimated sleep is below the personal baseline, but no recent "
                           "reaction-time result is available to assess performance.")
        else:
            status = "insufficient_data"
            explanation = ("There are not enough recent and baseline observations to assess "
                           "alertness. No fatigue conclusion is drawn.")
    elif sleep_reduced:
        status = "attention_needed"
        explanation = ("Recent estimated sleep is below the personal baseline, while reaction-time "
                       "performance remains near baseline. No performance decline is demonstrated.")
    elif schedule_shift:
        status = "attention_needed"
        explanation = ("The sleep schedule has shifted relative to the baseline period, but sleep "
                       "duration and reaction-time performance remain near baseline. A schedule "
                       "change alone does not indicate reduced concentration; continued "
                       "monitoring is suggested.")
    else:
        status = "normal"
        explanation = ("Recent estimated sleep and reaction-time performance are near this "
                       "astronaut's personal baseline.")

    used_records = ([s for s in sessions if s["sleep_session_id"] in
                     set(evidence["recent_sleep_session_ids"] + evidence["baseline_sleep_session_ids"])]
                    + [a for a in assessments if a["assessment_id"] in
                       set(evidence["recent_assessment_ids"] + evidence["baseline_assessment_ids"])])
    considered = used_records or sessions + assessments
    for night in nights:
        night["role"] = ("baseline" if night["night_id"] in
                         {n["night_id"] for n in (base_nights or [])}
                         else "recent" if night["night_id"] in
                         {n["night_id"] for n in (recent_nights or [])} else "other")

    return {
        "astronaut_id": astronaut_id,
        "mission_id": mission_id,
        "analysis_version": ANALYSIS_VERSION,
        "analyzed_at_utc": _fmt(datetime.now(timezone.utc).replace(microsecond=0)),
        "evaluated_at_utc": _fmt(evaluated_at),
        "assessment_timestamp_utc": latest_assessment["assessment_utc"] if latest_assessment else None,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "explanation": explanation,
        "suggested_action": suggested_action(status, task_context),
        "task_context": {"key": task_context, "label": TASK_CONTEXTS[task_context]},
        "supporting_observations": observations,
        "baseline_comparisons": comparisons,
        "data_quality_issues": issues,
        "rule_checks": checks,
        "evidence": evidence,
        "nights": nights,
        "is_synthetic_input": any(r["is_synthetic"] for r in considered),
        "input_scope": "synthetic_only" if synthetic_only else "all_records",
        "excluded_record_ids": excluded,
        "config": asdict(config),
        "limitations": LIMITATIONS,
    }


def _decline_text(rt_slowed: bool, lapses_up: bool) -> str:
    if rt_slowed and lapses_up:
        return "slower responses and more missed responses (lapses)"
    return "slower responses" if rt_slowed else "more missed responses (lapses)"


def _round(value, digits=2):
    return None if value is None else round(value, digits)


__all__ = ["ANALYSIS_VERSION", "AnalysisConfig", "DEFAULT_CONFIG", "STATUS_LABELS",
           "TASK_CONTEXTS", "analyze_alertness", "build_nights", "suggested_action"]
