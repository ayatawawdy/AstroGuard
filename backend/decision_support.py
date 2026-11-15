"""Decision-support orchestration used by the dashboard and data-entry flows.

    stored data -> analyze_alertness() -> record_notification() -> dashboard

``evaluate_and_notify`` is called whenever the dashboard renders a selection,
after a manual sleep log, after an actigraphy import and after a reaction-time
assessment is saved. It only evaluates stored data; deduplication in
``backend.notifications`` makes repeated calls safe.
"""

from __future__ import annotations

from contextlib import closing

from backend.alertness_analysis import DEFAULT_CONFIG, analyze_alertness
from backend.database import get_connection
from backend.notifications import record_notification

DEMO_MISSION_ID = "MISSION-SLEEP-DEMO-01"
DEMO_SCENARIOS = {
    "AST-S01": "Scenario 1 · Normal sleep and alertness",
    "AST-S02": "Scenario 2 · Shifted sleep schedule",
    "AST-S03": "Scenario 3 · Reduced sleep, slower reactions",
    "AST-S04": "Scenario 4 · Insufficient data",
}

# Interactive entries (browser reaction tests, dashboard sleep logs) are stored ONLY
# under this separate live test profile. The four fictional demo scenarios are
# protected so a live entry can never be mixed with their historical synthetic data.
LIVE_ASTRONAUT_ID = "LIVE-USER-01"
LIVE_DISPLAY_NAME = "Live test profile (interactive entries, not an astronaut)"
LIVE_MISSION_ID = "LIVE-INTERACTIVE-TESTS"
LIVE_MISSION_NAME = "Interactive testing session (real user entries, not a mission)"

_MISSION_SOURCES = ("measurements", "sleep_sessions", "alertness_assessments",
                    "cortisol_measurements", "actigraphy_recordings")


def _connect(db_path):
    return get_connection() if db_path is None else get_connection(db_path)


def ensure_live_profile(*, db_path=None) -> None:
    """Register the live test profile and its session (rows only; no measurements).

    INSERT OR IGNORE: existing rows, including display names, are untouched.
    """
    with closing(_connect(db_path)) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO astronauts (astronaut_id, display_name) VALUES (?, ?)",
                     (LIVE_ASTRONAUT_ID, LIVE_DISPLAY_NAME))
        conn.execute("INSERT OR IGNORE INTO missions (mission_id, mission_name) VALUES (?, ?)",
                     (LIVE_MISSION_ID, LIVE_MISSION_NAME))


def accepts_interactive_entries(astronaut_id: str, mission_id: str) -> bool:
    return astronaut_id == LIVE_ASTRONAUT_ID and mission_id == LIVE_MISSION_ID


def _require_interactive_target(astronaut_id: str, mission_id: str) -> None:
    if not accepts_interactive_entries(astronaut_id, mission_id):
        kind = get_profile_kind(astronaut_id)
        reason = {"demo": "is a protected fictional demo scenario",
                  "research": "holds imported public research data",
                  "legacy": "holds the legacy synthetic health dataset"}.get(kind, "is not writable")
        raise ValueError(f"{astronaut_id} {reason}; interactive entries are saved only to the "
                         f"live test profile {LIVE_ASTRONAUT_ID} / {LIVE_MISSION_ID}.")


def evaluate_and_notify(astronaut_id: str, mission_id: str, *,
                        task_context: str = "routine_monitoring",
                        config=DEFAULT_CONFIG, db_path=None) -> dict:
    """Analyze stored data and persist an alert event when warranted.

    Fictional demo scenarios are evaluated on their predefined synthetic
    records only, so their deterministic outcomes cannot be changed by real
    entries stored on those profiles (e.g. before profile isolation existed).
    """
    analysis = analyze_alertness(astronaut_id, mission_id, task_context=task_context,
                                 config=config,
                                 synthetic_only=get_profile_kind(astronaut_id) == "demo",
                                 db_path=db_path)
    event, created = record_notification(analysis, db_path=db_path)
    return {"analysis": analysis, "event": event, "new_event": created}


def save_manual_sleep_log(astronaut_id: str, mission_id: str, bedtime_utc, wake_time_utc, *,
                          task_context: str = "routine_monitoring", db_path=None,
                          **session_fields) -> dict:
    """Store a dashboard sleep log (manual, real) on the live profile, then re-evaluate."""
    from backend import sleep_alertness as sa

    _require_interactive_target(astronaut_id, mission_id)
    session_id = sa.add_sleep_session(astronaut_id, mission_id, bedtime_utc, wake_time_utc,
                                      data_origin="manual", is_synthetic=False,
                                      db_path=db_path, **session_fields)
    outcome = evaluate_and_notify(astronaut_id, mission_id, task_context=task_context,
                                  db_path=db_path)
    return {"sleep_session_id": session_id, **outcome}


BROWSER_TEST_PROTOCOL = "astroguard-pvt-inspired-demo-v0.1 (10 trials, 3 s timeout)"


def save_browser_assessment(payload: dict, astronaut_id: str, mission_id: str, *,
                            task_context: str = "routine_monitoring", now=None,
                            db_path=None) -> dict:
    """Store a result from the browser reaction-time component, then re-evaluate.

    ``payload`` is the component value: ``{"nonce", "started_at_utc", "trials",
    "trial_target"}``. The assessment time is the browser start time, clamped
    to the server clock so a fast client clock cannot create a future record.
    The assessment ID is derived from the nonce, so a resubmitted payload is
    stored once. Labeled ``data_origin='app'`` and not synthetic.
    """
    from datetime import datetime, timezone
    import hashlib

    from backend import sleep_alertness as sa

    _require_interactive_target(astronaut_id, mission_id)
    if not isinstance(payload, dict):
        raise ValueError("Reaction-test result must be a mapping")
    nonce, trials = payload.get("nonce"), payload.get("trials")
    if not isinstance(nonce, str) or not nonce or not isinstance(trials, list):
        raise ValueError("Reaction-test result is missing its nonce or trials")
    started = datetime.strptime(sa.normalize_utc(payload.get("started_at_utc"), "started_at_utc",
                                                 allow_future=True), sa.UTC_FORMAT)
    started = started.replace(tzinfo=timezone.utc)
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    target = payload.get("trial_target", len(trials))
    assessment_id = sa.add_alertness_assessment(
        astronaut_id, mission_id, min(started, now), trials,
        protocol_version=BROWSER_TEST_PROTOCOL, timing_method="browser_event_timestamp",
        data_origin="app", is_synthetic=False,
        completion_status="completed" if len(trials) >= target else "incomplete",
        notes="Interactive dashboard demonstration test (browser timing)",
        assessment_id="ALR-WEB-" + hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:16],
        db_path=db_path,
    )
    outcome = evaluate_and_notify(astronaut_id, mission_id, task_context=task_context,
                                  db_path=db_path)
    return {"assessment_id": assessment_id, **outcome}


def get_astronaut_missions(astronaut_id: str, *, db_path=None) -> list[dict]:
    """Missions with ANY stored record (legacy or sleep/alertness) for an astronaut."""
    if not isinstance(astronaut_id, str) or not astronaut_id.strip():
        raise ValueError("Astronaut ID must be a nonempty string")
    union = " UNION ".join(f"SELECT mission_id FROM {t} WHERE astronaut_id = ?"
                           for t in _MISSION_SOURCES)
    params = [astronaut_id] * len(_MISSION_SOURCES)
    if astronaut_id == LIVE_ASTRONAUT_ID:      # session exists before the first entry
        union += " UNION SELECT ?"
        params.append(LIVE_MISSION_ID)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            f"SELECT m.mission_id, m.mission_name FROM missions AS m "
            f"WHERE m.mission_id IN ({union}) ORDER BY m.mission_id",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_profile_kind(astronaut_id: str) -> str:
    """'demo' (fictional scenario), 'live' (interactive test profile),
    'research' (public Earth dataset) or 'legacy'."""
    if astronaut_id in DEMO_SCENARIOS:
        return "demo"
    if astronaut_id == LIVE_ASTRONAUT_ID:
        return "live"
    if astronaut_id.startswith("RES-"):
        return "research"
    return "legacy"
