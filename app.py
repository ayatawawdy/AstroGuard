"""AstroGuard Mission Control - sleep & alertness decision-support dashboard.

Research / hackathon prototype. Not a medical device, not a validated fatigue
detector and not a certified safety system. AstroGuard never pauses or stops
astronaut work; human operators and mission procedures retain authority.
"""

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from sqlite3 import DatabaseError

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from backend import actigraphy as ag
from backend import decision_support as ds
from backend import notifications as nt
from backend import sleep_alertness as sa
from backend.alertness_analysis import TASK_CONTEXTS
from backend.bootstrap import prepare_database
from backend.frontend_service import (
    analyze_astronaut_range,
    get_astronauts,
    get_measurements,
    get_missions,
)

APP_DIR = Path(__file__).resolve().parent
ZENODO_ARCHIVE = APP_DIR / "data" / "external" / "zenodo_4961281" / "Supplementary_Material_Final.zip"
UTC = timezone.utc

st.set_page_config(
    page_title="AstroGuard | Mission Control",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.stApp {
    background: linear-gradient(135deg, #080F1F 0%, #101D35 55%, #071326 100%);
    color: #EAF4FF;
}
.block-container { padding: 2rem 1.25rem; max-width: 1500px; }
header[data-testid="stHeader"] { background: rgba(8, 15, 31, 0.92); }
h1, h2, h3 { color: #F4F8FF !important; font-family: 'Segoe UI', sans-serif; }
/* Body copy only (never button labels or widget internals). */
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li { color: #D3E0F0; }
[data-testid="stCaptionContainer"] p { color: #A9BBD3 !important; }
[data-testid="stWidgetLabel"] p { color: #DCE7F5 !important; font-weight: 600; }
hr { border-color: #29476A !important; }

/* Buttons: readable in every state, independent of the Streamlit theme.
   Streamlit's stable data-testid hooks are used because a button with a tooltip
   is wrapped in an extra element. */
[data-testid^="stBaseButton-secondary"] {
    background: #172640 !important; color: #EAF4FF !important;
    border: 1px solid #4A6C99 !important; border-radius: 10px; min-height: 2.75rem;
}
[data-testid^="stBaseButton-secondary"]:hover {
    background: #1D3253 !important; border-color: #56DDF2 !important; color: #FFFFFF !important;
}
[data-testid^="stBaseButton-primary"] {
    background: #56DDF2 !important; color: #06121F !important;
    border: 2px solid #C9F6FC !important; border-radius: 10px; min-height: 2.75rem;
    box-shadow: 0 0 0 3px rgba(86, 221, 242, 0.28);
}
[data-testid^="stBaseButton-primary"]:hover { background: #8DEBF8 !important; }
[data-testid^="stBaseButton"] p {
    color: inherit !important; font-weight: 700; white-space: normal; line-height: 1.25;
}
[data-testid^="stBaseButton"]:focus-visible { outline: 3px solid #FFD166 !important; outline-offset: 2px; }

/* Metrics: wrap instead of truncating ("Rest Recommended", "Equipment Maintenance"). */
[data-testid="stMetric"] {
    background: #13233D; border: 1px solid #29476A; border-radius: 12px;
    padding: 10px 14px; height: 100%;
}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {
    white-space: normal !important; overflow: visible !important;
    text-overflow: clip !important; color: #A9BBD3 !important;
}
[data-testid="stMetricValue"] { color: #F4F8FF; font-size: 1.55rem !important; line-height: 1.25; }
[data-testid="stMetricValue"] > div, [data-testid="stMetricValue"] * {
    white-space: normal !important; overflow: visible !important; text-overflow: clip !important;
}

/* Tabs: wrap onto a second line on narrower screens instead of scrolling. */
[data-baseweb="tab-list"] { flex-wrap: wrap; row-gap: 4px; column-gap: 6px; }
[data-baseweb="tab"] { padding: 6px 12px; border-radius: 8px 8px 0 0; }
[data-baseweb="tab"] p { font-size: 0.98rem; font-weight: 600; }
[data-baseweb="tab"][aria-selected="true"] { background: #172640; }

/* Toasts and inline alerts: light text on dark surfaces. */
[data-testid="stToast"] { background: #172640 !important; border: 1px solid #56DDF2; }
[data-testid="stToast"] p { color: #EAF4FF !important; }
[data-testid="stAlert"] p, [data-testid="stAlert"] li { color: inherit; }
</style>
""", unsafe_allow_html=True)

STATUS_STYLE = {
    "normal": ("#123B36", "#68F0C0", "✅", "NORMAL"),
    "attention_needed": ("#3B2F10", "#FFB547", "⚠️", "ATTENTION NEEDED"),
    "rest_recommended": ("#3D1216", "#FF6B6B", "🛑", "REST RECOMMENDED"),
    "insufficient_data": ("#1E2A3D", "#A9B8CC", "ℹ️", "INSUFFICIENT DATA"),
}
STATUS_SUBTITLE = {
    "normal": "No meaningful change from the personal baseline detected.",
    "attention_needed": "A change from the personal baseline is worth monitoring.",
    "rest_recommended": "Potential reduction in alertness detected.",
    "insufficient_data": "Not enough data to assess alertness. No conclusion is drawn.",
}
ROLE_COLORS = {"baseline": "#56DDF2", "recent": "#FF9F6B", "other": "#5B6F8C"}
UTC_OFFSETS = [f"{'+' if h >= 0 else '-'}{abs(h):02d}:00" for h in range(-12, 15)]


def html_escape(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def dark(fig: go.Figure, title: str, height: int = 340) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=18, color="#F4F8FF")),
        paper_bgcolor="#172640", plot_bgcolor="#172640", font=dict(color="#B8CBE4"),
        height=height, margin=dict(l=40, r=25, t=60, b=40),
        legend=dict(orientation="h", y=-0.2),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#29476A", zeroline=False)
    return fig


def show_chart(fig):
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})


def parse_utc(text):
    return datetime.strptime(text, sa.UTC_FORMAT).replace(tzinfo=UTC)


def flash(kind: str, message: str):
    st.session_state.setdefault("flash", []).append((kind, message))


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Preparing mission database...")
def ensure_database_ready():
    """Create tables and import demo data once per server process."""
    prepare_database()
    return True


try:
    ensure_database_ready()
except (DatabaseError, OSError, RuntimeError, ValueError) as error:
    st.error(f"Could not prepare the mission database: {error}")
    st.stop()

try:
    astronauts = get_astronauts()
except DatabaseError:
    st.error("Could not load astronauts. Check that the mission database is initialized.")
    st.stop()
if not astronauts:
    st.warning("No astronauts found. Import the mission data before opening the dashboard.")
    st.stop()

kind_order = {"demo": 0, "live": 1, "legacy": 2, "research": 3}
astronauts = sorted(astronauts, key=lambda a: (kind_order[ds.get_profile_kind(a["astronaut_id"])],
                                               a["astronaut_id"]))
labels = {}
for row in astronauts:
    aid, name = row["astronaut_id"], row["display_name"] or row["astronaut_id"]
    kind = ds.get_profile_kind(aid)
    labels[aid] = (f"{aid} · {ds.DEMO_SCENARIOS[aid]} (fictional)" if kind == "demo"
                   else f"{aid} · 🧪 Live test profile (your interactive entries)" if kind == "live"
                   else f"{aid} · ⌚ Real research data (Earth participant, not an astronaut)"
                   if kind == "research"
                   else f"{name} — legacy health demo")
options = list(labels)
if st.session_state.get("pending_select") in labels:      # e.g. after an import
    st.session_state["astronaut_select"] = st.session_state.pop("pending_select")
if "astronaut_select" not in st.session_state or st.session_state["astronaut_select"] not in labels:
    st.session_state["astronaut_select"] = "AST-S01" if "AST-S01" in labels else options[0]
st.session_state.setdefault("sounded_events", [])
st.session_state.setdefault("rt_processed", [])

# ---------------------------------------------------------------------------
# Header and selection
# ---------------------------------------------------------------------------

st.html("""
<div style="padding:28px 32px; background:linear-gradient(120deg,#142743,#0C1830); border:1px solid #29476A; border-radius:20px; margin-bottom:18px;">
    <div style="color:#56DDF2; font-size:13px; letter-spacing:3px; font-weight:700;">MISSION CONTROL / SLEEP & ALERTNESS DECISION SUPPORT</div>
    <h1 style="font-size:44px; margin:12px 0 6px; color:#FFFFFF;">🚀 AstroGuard</h1>
    <p style="font-size:18px; color:#B8CBE4; margin:0;">Personalized astronaut sleep and alertness decision support · research prototype</p>
    <div style="display:inline-block; margin-top:14px; background:#1E2A3D; color:#DCE7F5; border:1px solid #3A5A85; padding:6px 14px; border-radius:16px; font-size:13px;">
        DEMO MODE · evaluates stored records on demand · no live wearable connection
    </div>
</div>
""")

demo_ids = [a for a in ds.DEMO_SCENARIOS if a in labels]
if demo_ids:
    st.markdown("**Demo scenarios** · fictional astronauts with synthetic, historical data")
    current = st.session_state["astronaut_select"]
    columns = st.columns(len(demo_ids))
    for col, aid in zip(columns, demo_ids):
        selected = aid == current
        col.button(("✓ " if selected else "") + ds.DEMO_SCENARIOS[aid], key=f"scenario_{aid}",
                   width="stretch", type="primary" if selected else "secondary",
                   help=f"Show {aid} ({'currently selected' if selected else 'click to select'})",
                   on_click=st.session_state.update, kwargs={"astronaut_select": aid})
if ds.LIVE_ASTRONAUT_ID in labels:
    live_selected = st.session_state["astronaut_select"] == ds.LIVE_ASTRONAUT_ID
    l1, l2 = st.columns([1.4, 3])
    l1.button(("✓ " if live_selected else "") + "🧪 Live test profile", key="live_profile",
              width="stretch", type="primary" if live_selected else "secondary",
              help="Separate profile for interactive reaction tests and sleep logs. The four "
                   "demo scenarios above are protected and never receive live entries.",
              on_click=st.session_state.update,
              kwargs={"astronaut_select": ds.LIVE_ASTRONAUT_ID})
    l2.caption("Interactive testing: reaction tests and sleep logs are saved only to this "
               "separate profile (real entries, evaluated against its own history).")
research_ids = [a for a in labels if ds.get_profile_kind(a) == "research"]
if research_ids:
    research_selected = st.session_state["astronaut_select"] in research_ids
    r1, r2 = st.columns([1.4, 3])
    r1.button(("✓ " if research_selected else "") + "⌚ Real Actiwatch research data",
              key="research_profile", width="stretch",
              type="primary" if research_selected else "secondary",
              help="Real recordings from Earth research participants (Zenodo 4961281), "
                   "not astronauts.", on_click=st.session_state.update,
              kwargs={"astronaut_select": research_ids[0]})
    r2.caption(f"{len(research_ids)} imported research participant(s): real Actiwatch Spectrum "
               "Pro recordings from people on Earth (not astronauts). Open 🌙 Sleep → "
               "⌚ Actigraphy (recorded).")

c1, c2, c3, c4 = st.columns([3.3, 2.1, 2.3, 1.5])
astronaut_id = c1.selectbox("Select astronaut", options, format_func=labels.__getitem__,
                            key="astronaut_select")
try:
    missions = ds.get_astronaut_missions(astronaut_id)
except DatabaseError:
    st.error("Could not load missions from the database.")
    st.stop()
if not missions:
    st.info("No missions found for this astronaut.")
    st.stop()
mission_names = {m["mission_id"]: m["mission_name"] or m["mission_id"] for m in missions}
mission_id = c2.selectbox("Select mission", list(mission_names),
                          key=f"mission_{astronaut_id}",
                          help="Full mission name is shown below the selector.")
if mission_names[mission_id] != mission_id:
    c2.caption(mission_names[mission_id])
task_context = c3.selectbox("Mission task context", list(TASK_CONTEXTS),
                            format_func=TASK_CONTEXTS.__getitem__, key="task_context")
sound_on = c4.toggle("🔊 Alert sound", value=False,
                     help="Plays a short browser tone once for a new Rest Recommended alert. "
                          "This is a dashboard sound, not a command to the wristwatch.")

for kind, message in st.session_state.pop("flash", []):
    getattr(st, kind)(message)

# ---------------------------------------------------------------------------
# Evaluate stored data -> analysis -> persistent notification
# ---------------------------------------------------------------------------

try:
    result = ds.evaluate_and_notify(astronaut_id, mission_id, task_context=task_context)
    sessions = sa.get_sleep_sessions(astronaut_id, mission_id=mission_id)
    assessments = sa.get_alertness_assessments(astronaut_id, mission_id=mission_id)
    cortisol = sa.get_cortisol_measurements(astronaut_id, mission_id=mission_id)
    unacked = nt.count_unacknowledged(astronaut_id, mission_id=mission_id)
except (DatabaseError, ValueError) as error:
    st.error(f"Could not evaluate the selected astronaut: {error}")
    st.stop()

analysis, event = result["analysis"], result["event"]
status = analysis["status"]
# Charts and metrics use the same records as the analysis. On protected demo profiles,
# real interactive records are listed (labeled) in the assessments table only.
excluded_ids = set(analysis["excluded_record_ids"])
all_assessments = assessments
sessions = [s for s in sessions if s["sleep_session_id"] not in excluded_ids]
assessments = [a for a in assessments if a["assessment_id"] not in excluded_ids]
cortisol = [c for c in cortisol if c["cortisol_id"] not in excluded_ids]


def fmt_utc(text) -> str:
    """'2026-10-15T07:56:00Z' -> '15 Oct 2026, 07:56 UTC'."""
    if not text:
        return "n/a"
    return parse_utc(text).strftime("%d %b %Y, %H:%M UTC")


# Data mode: distinguishes historical/synthetic records from recent entries.
observation_times = ([s["bedtime_utc"] for s in sessions] + [s["wake_time_utc"] for s in sessions]
                     + [a["assessment_utc"] for a in assessments])
first_obs = min(observation_times) if observation_times else None
last_obs = analysis["evaluated_at_utc"]
is_historical = bool(last_obs) and datetime.now(UTC) - parse_utc(last_obs) > timedelta(hours=48)
profile_kind = ds.get_profile_kind(astronaut_id)
if profile_kind == "live":
    data_mode = "LIVE TEST PROFILE · interactive entries (real, not synthetic)"
elif profile_kind == "research":
    data_mode = "HISTORICAL REAL RESEARCH RECORDING · Earth participant, not an astronaut"
elif analysis["is_synthetic_input"]:
    data_mode = "HISTORICAL / SYNTHETIC DEMO"
elif not last_obs:
    data_mode = "NO STORED OBSERVATIONS"
elif is_historical:
    data_mode = "HISTORICAL DATA (real / user-entered)"
else:
    data_mode = "RECENT STORED DATA (real / user-entered)"

if result["new_event"]:
    st.toast(f"Alert recorded: {event['title']} for {astronaut_id}, based on observations up to "
             f"{fmt_utc(last_obs)}", icon="🔔")


def acknowledge(event_id: str, owner: str):
    try:
        nt.acknowledge_notification(event_id, owner)
        flash("success", f"Alert {event_id} acknowledged and saved. Acknowledgment does not mean "
                         "the astronaut is rested, and the assessment itself is unchanged.")
    except (DatabaseError, ValueError) as error:
        flash("error", f"Could not acknowledge the alert: {error}")


def play_alert_sound(event_id: str):
    """One short browser tone per event and browser session (autoplay permitting)."""
    components.html(f"""
<div style="font-family:Segoe UI,sans-serif;color:#FFB4B4;font-size:13px">
  <button id="b" style="background:#FF6B6B;border:0;border-radius:8px;padding:6px 12px;
    font-weight:700;cursor:pointer">🔊 Play alert tone</button>
  <span id="m"> Alert tone for {html_escape(event_id)}</span>
</div>
<script>
function tone() {{
  try {{
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [0, 0.35].forEach(function (t) {{
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine"; o.frequency.value = 880; g.gain.value = 0.15;
      o.connect(g); g.connect(ctx.destination);
      o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.25);
    }});
    return ctx.state === "running";
  }} catch (e) {{ return false; }}
}}
document.getElementById("b").onclick = tone;
if (!tone()) {{ document.getElementById("m").textContent =
  " Browser blocked autoplay - press the button to hear the alert tone."; }}
</script>""", height=48)


# ---------------------------------------------------------------------------
# Status banner
# ---------------------------------------------------------------------------

bg, fg, icon, headline = STATUS_STYLE[status]
synthetic_badge = ("HISTORICAL / SYNTHETIC DEMO DATA" if analysis["is_synthetic_input"]
                   else "REAL RESEARCH DATA (EARTH PARTICIPANT)" if profile_kind == "research"
                   else "REAL / USER-ENTERED DATA")
period = (f"observations recorded {fmt_utc(first_obs)} → {fmt_utc(last_obs)}" if last_obs
          else "no stored observations yet")
st.html(f"""
<div style="background:#13233D; border:1px solid #3A5A85; border-left:6px solid #56DDF2; border-radius:12px; padding:10px 16px; margin:6px 0 4px; color:#DCE7F5; font-size:14px;">
  <b style="color:#56DDF2;">🗂️ {html_escape(data_mode)}</b> · {html_escape(period)}<br>
  <span style="color:#A9BBD3;">Status is computed from stored records when data is saved, a scenario is
  selected or the page refreshes. This is not live monitoring and there is no live connection to a wearable device.</span>
</div>
""")
if profile_kind == "demo":
    st.caption("🔒 Protected demo scenario: evaluated only on its predefined synthetic records. "
               "Interactive tests and sleep logs are saved to the 🧪 Live test profile instead.")
if analysis["excluded_record_ids"]:
    st.info(f"{len(analysis['excluded_record_ids'])} real interactive record(s) stored on this "
            "demo profile (saved before demo profiles were protected) are kept in the database "
            "but excluded from this scenario's analysis: "
            + ", ".join(analysis["excluded_record_ids"]) + ". Any alert they produced remains "
            "in the 🔔 Alerts history.")
st.html(f"""
<div style="background:{bg}; border:2px solid {fg}; border-radius:18px; padding:22px 26px; margin:8px 0 14px;">
  <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px;">
    <div style="color:{fg}; font-size:30px; font-weight:800; letter-spacing:1px;">{icon} {headline}</div>
    <div style="color:#EAF4FF; background:#0B1426; border-radius:14px; padding:6px 12px; font-size:12px; height:fit-content;">● {synthetic_badge}</div>
  </div>
  <div style="color:#F4F8FF; font-size:17px; margin-top:6px; font-weight:600;">{STATUS_SUBTITLE[status]}</div>
  <div style="color:#D9E5F5; font-size:15px; margin-top:10px;">{html_escape(analysis['explanation'])}</div>
  <div style="color:#F4F8FF; font-size:15px; margin-top:10px;"><b>Suggested action:</b> {html_escape(analysis['suggested_action'])}</div>
  <div style="color:#C2D0E2; font-size:13px; margin-top:12px; line-height:1.6;">
    <b>Data observed up to:</b> {html_escape(fmt_utc(last_obs))} &nbsp;·&nbsp;
    <b>Analysis run:</b> {html_escape(fmt_utc(analysis['analyzed_at_utc']))}<br>
    Astronaut {html_escape(astronaut_id)} · Mission {html_escape(mission_id)} · Analysis {html_escape(analysis['analysis_version'])} · Decision support only - never stops or pauses a task.
  </div>
</div>
""")

if event is not None:
    ack_text = (f"✔ Acknowledged {fmt_utc(event['acknowledged_utc'])} by {event['acknowledged_by']}"
                if event["acknowledged"] else "⏳ Not yet acknowledged")
    a1, a2 = st.columns([4, 1.3])
    a1.markdown(f"**Alert record** `{event['event_id']}` · {ack_text}  \n"
                f"Alert created **{fmt_utc(event['created_utc'])}** (when AstroGuard first "
                f"evaluated this evidence) · based on observations up to "
                f"**{fmt_utc(event['evaluated_at_utc'])}**")
    if not event["acknowledged"]:
        a2.button("Acknowledge Alert", key=f"ack_banner_{event['event_id']}", type="primary",
                  on_click=acknowledge, args=(event["event_id"], astronaut_id))
        if (sound_on and status == "rest_recommended"
                and event["event_id"] not in st.session_state["sounded_events"]):
            st.session_state["sounded_events"].append(event["event_id"])
            play_alert_sound(event["event_id"])

nights = analysis["nights"]
usable = [n for n in nights if n["usable"]]
completed = [a for a in assessments if a["completion_status"] == "completed"]
m1, m2, m3, m4, m5 = st.columns([1.3, 1.1, 1.1, 1, 1.3])
m1.metric("Alertness status", analysis["status_label"])
if usable:
    last = usable[-1]
    m2.metric("Latest night · est. sleep",
              f"{last['estimated_sleep_minutes'] / 60:.1f} h",
              help=f"Latest recorded night, starting {fmt_utc(last['bedtime_utc'])}. Time in bed "
                   f"{last['time_in_bed_minutes'] / 60:.1f} h; time in bed is not the same as sleep.")
else:
    m2.metric("Latest night · est. sleep", "n/a")
m3.metric("Latest test · median RT",
          f"{completed[-1]['median_rt_ms']:.0f} ms" if completed and completed[-1]["median_rt_ms"]
          else "n/a",
          help=(f"Assessment recorded {fmt_utc(completed[-1]['assessment_utc'])}." if completed
                else "No completed reaction-time assessment stored."))
m4.metric("Unacknowledged alerts", unacked,
          help="Open alerts for this astronaut and mission (all alerts: Alerts tab).")
m5.metric("Task context", TASK_CONTEXTS[task_context])

open_alerts = nt.count_unacknowledged()
(top_overview, top_sleep, top_alertness, tab_notify, top_more) = st.tabs([
    "🛰️ Overview & Decision", "🌙 Sleep", "⚡ Alertness",
    f"🔔 Alerts · {open_alerts} open" if open_alerts else "🔔 Alerts", "🗂️ More Data"])
with top_overview:
    tab_overview, tab_decision = st.tabs(["Summary", "Decision support & evidence"])
with top_sleep:
    tab_sleep, tab_log, tab_acti = st.tabs(["Sleep monitoring", "📝 Log sleep",
                                            "⌚ Actigraphy (recorded)"])
with top_alertness:
    tab_alert, tab_test = st.tabs(["Reaction-time trends", "⏱️ Reaction test"])
with top_more:
    tab_cortisol, tab_legacy = st.tabs(["🧪 Cortisol (context)", "❤️ Legacy health"])

# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

with tab_overview:
    st.subheader("Mission overview")
    left, right = st.columns(2)
    with left:
        st.markdown("**Why this status**")
        for obs in analysis["supporting_observations"]:
            st.markdown(f"- {obs['text']}")
        if not analysis["supporting_observations"]:
            st.markdown("- No supporting observations available.")
    with right:
        st.markdown("**Data availability**")
        st.markdown(f"- {len(sessions)} sleep record(s), {len(usable)} night(s) with a usable "
                    f"sleep estimate\n- {len(assessments)} reaction-time assessment(s) "
                    f"({len(completed)} completed)\n- {len(cortisol)} optional cortisol sample(s)")
        if analysis["data_quality_issues"]:
            st.markdown(f"- ⚠️ {len(analysis['data_quality_issues'])} data-quality issue(s); see "
                        "Decision Support")
    st.caption("Fictional demo astronauts use synthetic data. Real research data comes from "
               "Earth-based participants, not astronauts.")

# ---------------------------------------------------------------------------
# Sleep monitoring
# ---------------------------------------------------------------------------

with tab_sleep:
    st.subheader("Sleep duration vs personal baseline")
    if not nights:
        st.info("No sleep records for this astronaut and mission yet. Use **Log Sleep** or "
                "import actigraphy data.")
    else:
        frame = pd.DataFrame(nights)
        frame["night"] = [(parse_utc(b) - timedelta(hours=12)).date().isoformat()
                          for b in frame["bedtime_utc"]]
        fig = go.Figure()
        for role, color in ROLE_COLORS.items():
            part = frame[frame["role"] == role]
            if not part.empty:
                fig.add_bar(x=part["night"], y=part["estimated_sleep_minutes"] / 60, name=(
                    f"Estimated sleep · {role}"), marker_color=color)
        fig.add_scatter(x=frame["night"], y=frame["time_in_bed_minutes"] / 60, mode="markers",
                        name="Time in bed", marker=dict(symbol="line-ew-open", size=18,
                                                        color="#F4F8FF"))
        sleep_cmp = analysis["baseline_comparisons"].get("sleep")
        if sleep_cmp:
            fig.add_hline(y=sleep_cmp["baseline_mean_sleep_minutes"] / 60, line_dash="dash",
                          line_color="#56DDF2", annotation_text="personal baseline")
        fig.update_yaxes(title="hours", rangemode="tozero")
        show_chart(dark(fig, "Estimated sleep per night (main sleep)"))
        st.caption("Bars = estimated actual sleep; markers = time in bed. Nights without a usable "
                   "estimate show no bar. Baseline = earliest usable nights only.")

        st.subheader("Sleep schedule and regularity")
        sched = go.Figure()
        for _, row in frame.iterrows():
            bed = parse_utc(row["bedtime_utc"])
            noon = datetime.combine(date.fromisoformat(row["night"]), time(12), UTC)
            sched.add_bar(y=[row["night"]], x=[row["time_in_bed_minutes"] / 60],
                          base=[(bed - noon).total_seconds() / 3600], orientation="h",
                          marker_color=ROLE_COLORS[row["role"]], showlegend=False,
                          hovertext=f"{row['bedtime_utc']} → {row['wake_time_utc']} "
                                    f"({row['data_origin']})")
        sched.update_xaxes(title="UTC clock time", tickvals=list(range(0, 31, 3)),
                           ticktext=[f"{(12 + v) % 24:02d}:00" for v in range(0, 31, 3)])
        show_chart(dark(sched, "Time in bed by night (UTC)", height=380))
        schedule = analysis["baseline_comparisons"].get("schedule")
        if schedule:
            s1, s2, s3 = st.columns(3)
            s1.metric("Mid-sleep shift vs baseline", f"{schedule['mid_sleep_shift_hours']:+.1f} h")
            s2.metric("Baseline mid-sleep spread",
                      "n/a" if schedule["baseline_mid_sleep_spread_hours"] is None
                      else f"{schedule['baseline_mid_sleep_spread_hours']:.1f} h")
            s3.metric("Recent mid-sleep spread",
                      "n/a" if schedule["recent_mid_sleep_spread_hours"] is None
                      else f"{schedule['recent_mid_sleep_spread_hours']:.1f} h")
            st.caption("A shifted schedule alone is not treated as evidence of reduced alertness.")
        night_issues = [i for i in analysis["data_quality_issues"] if i.startswith("Night of")]
        for issue in night_issues:
            st.warning(f"Missing data: {issue}")

        st.markdown("**Sleep records and sources**")
        table = pd.DataFrame([{
            "type": s["sleep_type"], "bedtime (UTC)": s["bedtime_utc"],
            "wake (UTC)": s["wake_time_utc"],
            "time in bed (h)": round(s["time_in_bed_minutes"] / 60, 2),
            "estimated sleep (h)": None if s["estimated_sleep_minutes"] is None
            else round(s["estimated_sleep_minutes"] / 60, 2),
            "source": {"manual": "Manual log", "wearable": "Wearable estimate",
                       "synthetic": "Synthetic"}[s["data_origin"]],
            "synthetic": s["is_synthetic"], "sensor quality": s["sensor_quality"],
            "coverage": s["sensor_coverage_fraction"], "notes": s["missing_data_notes"] or s["notes"],
        } for s in sessions])
        st.dataframe(table, width="stretch", hide_index=True)
        st.caption("Sleep stages are not displayed: wrist actigraphy such as Actiwatch estimates "
                   "sleep/wake from movement and does not measure REM or deep sleep.")

# ---------------------------------------------------------------------------
# Alertness
# ---------------------------------------------------------------------------

with tab_alert:
    st.subheader("Reaction-time performance vs personal baseline")
    if not all_assessments:
        st.info("No reaction-time assessments yet. Use **⚡ Alertness → ⏱️ Reaction test** on "
                "the 🧪 Live test profile to record one.")
    elif not assessments:
        st.info("No in-scope assessments to chart for this profile.")
    if assessments:
        af = pd.DataFrame(assessments)
        af["time"] = pd.to_datetime(af["assessment_utc"], utc=True)
        rt_cmp = analysis["baseline_comparisons"].get("reaction_time")
        fig = go.Figure()
        if rt_cmp:
            fig.add_hrect(y0=rt_cmp["baseline_mean_median_rt_ms"],
                          y1=rt_cmp["baseline_mean_median_rt_ms"] + rt_cmp["slowing_margin_ms"],
                          fillcolor="#56DDF2", opacity=0.08, line_width=0)
            fig.add_hline(y=rt_cmp["baseline_mean_median_rt_ms"], line_dash="dash",
                          line_color="#56DDF2", annotation_text="personal baseline")
        fig.add_scatter(x=af["time"], y=af["median_rt_ms"], mode="lines+markers",
                        name="Median reaction time", line=dict(color="#FF9F6B", width=3))
        fig.update_yaxes(title="ms")
        show_chart(dark(fig, "Median reaction time per assessment"))
        lap = go.Figure()
        lap.add_bar(x=af["time"], y=af["lapse_count"], name="Lapses (missed / ≥ lapse threshold)",
                    marker_color="#FF6B6B")
        lap.add_bar(x=af["time"], y=af["false_start_count"], name="False starts",
                    marker_color="#FFB547")
        show_chart(dark(lap, "Lapses and false starts", height=300))
    if all_assessments:
        af = pd.DataFrame(all_assessments)
        af["source"] = [("interactive browser test (real)" if not syn else "synthetic scenario")
                        + (" · excluded from scenario analysis"
                           if aid_ in analysis["excluded_record_ids"] else "")
                        for syn, aid_ in zip(af["is_synthetic"], af["assessment_id"])]
        st.dataframe(af[["assessment_utc", "source", "completion_status", "trial_count", "median_rt_ms",
                         "mean_rt_ms", "lapse_count", "false_start_count",
                         "self_reported_fatigue", "data_origin", "is_synthetic",
                         "protocol_version"]], width="stretch", hide_index=True)
        chosen = st.selectbox("Inspect trials of assessment", [a["assessment_id"] for a in
                                                                reversed(all_assessments)],
                              key=f"trials_{astronaut_id}_{mission_id}")
        st.dataframe(pd.DataFrame(sa.get_reaction_time_trials(chosen)), width="stretch",
                     hide_index=True)
        st.caption("PVT-inspired measure. Not a clinically validated psychomotor vigilance test.")

# ---------------------------------------------------------------------------
# Decision support
# ---------------------------------------------------------------------------

with tab_decision:
    st.subheader("Decision support")
    st.markdown(f"**Status:** {analysis['status_label']}  \n**Task context:** "
                f"{analysis['task_context']['label']}  \n**Recommended action:** "
                f"{analysis['suggested_action']}")
    st.markdown("**Supporting evidence**")
    for obs in analysis["supporting_observations"]:
        st.markdown(f"- {obs['text']}  \n  <small>records: {', '.join(obs['record_ids'][:6])}"
                    f"{' …' if len(obs['record_ids']) > 6 else ''}</small>",
                    unsafe_allow_html=True)
    if analysis["rule_checks"]:
        st.markdown("**Transparent rule checks (illustrative, configurable thresholds)**")
        st.dataframe(pd.DataFrame(analysis["rule_checks"]), width="stretch", hide_index=True)
    st.markdown("**Missing information / data quality**")
    if analysis["data_quality_issues"]:
        for issue in analysis["data_quality_issues"]:
            st.markdown(f"- {issue}")
    else:
        st.markdown("- None detected.")
    with st.expander("Baseline comparisons and configuration"):
        st.json({"baseline_comparisons": analysis["baseline_comparisons"],
                 "config": analysis["config"]})
    st.markdown("**Limitations**")
    for item in analysis["limitations"]:
        st.markdown(f"- {item}")

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

with tab_notify:
    st.subheader("Notification history")
    st.caption("“Alert created” is when AstroGuard first evaluated the evidence. “Data observed "
               "up to” is the time of the latest underlying observation. For the demo crew this "
               "is historical synthetic data from October 2026.")
    only_selected = st.toggle("Show only the selected astronaut", value=False, key="notify_filter")
    history = nt.get_notifications(astronaut_id if only_selected else None)
    if not history:
        st.info("No alerts stored yet. Alerts are created automatically when an evaluation "
                "returns Attention Needed or Rest Recommended.")
    else:
        st.dataframe(pd.DataFrame([{
            "alert created (UTC)": fmt_utc(e["created_utc"]),
            "data observed up to (UTC)": fmt_utc(e["evaluated_at_utc"]),
            "astronaut": e["astronaut_id"],
            "mission": e["mission_id"], "category": e["title"],
            "explanation": e["explanation"][:140] + ("…" if len(e["explanation"]) > 140 else ""),
            "acknowledged": "yes" if e["acknowledged"] else "no",
            "acknowledged at": fmt_utc(e["acknowledged_utc"]) if e["acknowledged_utc"] else "",
            "synthetic input": e["is_synthetic_input"],
        } for e in history]), width="stretch", hide_index=True)
        for e in history[:20]:
            with st.expander(f"{e['astronaut_id']} · {e['title']} · "
                             f"{'acknowledged' if e['acknowledged'] else 'UNACKNOWLEDGED'} · "
                             f"data up to {fmt_utc(e['evaluated_at_utc'])} · alert created "
                             f"{fmt_utc(e['created_utc'])}"):
                st.markdown(f"**Explanation:** {e['explanation']}")
                st.markdown(f"**Suggested action (at creation, {TASK_CONTEXTS[e['task_context']]})"
                            f":** {e['suggested_action']}")
                st.markdown(f"**Data observed up to:** {fmt_utc(e['evaluated_at_utc'])} · "
                            f"**Alert created:** {fmt_utc(e['created_utc'])} · "
                            f"**Source assessment:** {e['source_assessment_id']}")
                for obs in e["evidence"]["supporting_observations"]:
                    st.markdown(f"- {obs['text']}")
                st.json(e["evidence"]["record_ids"], expanded=False)
                if not e["acknowledged"]:
                    if e["astronaut_id"] == astronaut_id:
                        st.button("Acknowledge Alert", key=f"ack_hist_{e['event_id']}",
                                  on_click=acknowledge, args=(e["event_id"], e["astronaut_id"]))
                    else:
                        st.caption("Select this astronaut to acknowledge this alert.")

# ---------------------------------------------------------------------------
# Manual sleep logging
# ---------------------------------------------------------------------------

with tab_log:
    st.subheader("Log a sleep period (manual report)")
    st.caption("Bedtime and wake time describe **time in bed**. Enter an estimate of actual "
               "sleep only if you can; time in bed is never counted as sleep.")
    if not ds.accepts_interactive_entries(astronaut_id, mission_id):
        st.info("🔒 Sleep logs are saved only to the separate **🧪 Live test profile**, so they can "
                "never be mixed with the predefined demo scenarios or imported research data.")
        st.button("Switch to the 🧪 Live test profile", key="switch_live_sleep",
                  type="primary", on_click=st.session_state.update,
                  kwargs={"astronaut_select": ds.LIVE_ASTRONAUT_ID},
                  disabled=ds.LIVE_ASTRONAUT_ID not in labels)
    else:
        with st.form("sleep_log", clear_on_submit=False):
            today = datetime.now(UTC).date()
            f1, f2, f3 = st.columns(3)
            bed_date = f1.date_input("Bedtime date", value=today - timedelta(days=1), max_value=today,
                                     key="log_bed_date")
            bed_time = f1.time_input("Bedtime", value=time(22, 30), step=300, key="log_bed_time")
            wake_date = f2.date_input("Wake date", value=today, max_value=today, key="log_wake_date")
            wake_time = f2.time_input("Wake time", value=time(6, 30), step=300, key="log_wake_time")
            offset = f3.selectbox("Time zone (UTC offset)", UTC_OFFSETS,
                                  index=UTC_OFFSETS.index("+00:00"), key="log_offset")
            sleep_type = f3.radio("Sleep type", ["main", "nap"], horizontal=True,
                                  format_func={"main": "Main sleep", "nap": "Nap"}.__getitem__,
                                  key="log_type")
            g1, g2 = st.columns(2)
            rate = g1.checkbox("Rate sleep quality", value=True, key="log_rate")
            quality = g1.slider("Sleep quality (1 = very poor, 5 = very good)", 1, 5, 3,
                                key="log_quality")
            estimate_known = g2.checkbox("I can estimate actual sleep time", value=False,
                                         key="log_estimate_known")
            estimate_h = g2.number_input("Estimated actual sleep (hours)", 0.0, 24.0, 7.0, 0.25,
                                         key="log_estimate_hours")
            notes = st.text_input("Notes (optional)", key="log_notes")
            submitted = st.form_submit_button("Save sleep log", key="log_submit")
        if submitted:
            sign = 1 if offset.startswith("+") else -1
            tz = timezone(sign * timedelta(hours=int(offset[1:3])))
            bed = datetime.combine(bed_date, bed_time, tz)
            wake = datetime.combine(wake_date, wake_time, tz)
            try:
                outcome = ds.save_manual_sleep_log(
                    astronaut_id, mission_id, bed, wake, task_context=task_context,
                    sleep_type=sleep_type, self_reported_quality=quality if rate else None,
                    estimated_sleep_minutes=estimate_h * 60 if estimate_known else None,
                    notes=(notes or None) and f"Dashboard entry: {notes}",
                )
                new_id = outcome["sleep_session_id"]
            except (DatabaseError, ValueError) as error:
                st.error(f"Sleep log not saved: {error}")
            else:
                flash("success", f"Sleep log {new_id} saved ({bed.astimezone(UTC):%Y-%m-%d %H:%M} → "
                                 f"{wake.astimezone(UTC):%Y-%m-%d %H:%M} UTC). Re-evaluated status: "
                                 f"{outcome['analysis']['status_label']}.")
                st.rerun()

# ---------------------------------------------------------------------------
# Interactive reaction-time test
# ---------------------------------------------------------------------------

reaction_component = components.declare_component(
    "astroguard_reaction_test", path=str(APP_DIR / "components" / "reaction_test"))

with tab_test:
    st.subheader("PVT-inspired reaction-time test (demonstration mode)")
    st.markdown("""
1. Press **Start assessment** and keep your eyes on the panel.
2. After a random wait (2–7 s) a red millisecond counter appears.
3. Click the panel or press **Space** as fast as possible.
4. Responding before the counter appears is a **false start**; no response within 3 s is a
   **missed response (lapse)**. Responses ≥ 500 ms also count as lapses.
5. After 10 trials the result is saved to SQLite and alertness is re-evaluated.
""")
    st.caption("Browser-side timing (high-resolution clock); display and input latency still "
               "apply. This short demonstration is NOT a clinically validated PVT and is not "
               "comparable to a standard 10-minute PVT. Results are saved to the live test profile "
               "only; the demo scenarios keep their predefined synthetic assessments.")
    if not ds.accepts_interactive_entries(astronaut_id, mission_id):
        st.info("🔒 Reaction tests are saved only to the separate **🧪 Live test profile**, so they can "
                "never be mixed with the predefined demo scenarios or imported research data.")
        st.button("Switch to the 🧪 Live test profile", key="switch_live_react",
                  type="primary", on_click=st.session_state.update,
                  kwargs={"astronaut_select": ds.LIVE_ASTRONAUT_ID},
                  disabled=ds.LIVE_ASTRONAUT_ID not in labels)
    else:
        test_result = reaction_component(trials=10, min_isi_ms=2000, max_isi_ms=7000,
                                         timeout_ms=3000, lapse_ms=500, false_start_ms=100,
                                         key=f"rt_component_{astronaut_id}_{mission_id}",
                                         default=None)
        if isinstance(test_result, dict) and test_result.get("nonce") \
                and test_result["nonce"] not in st.session_state["rt_processed"]:
            st.session_state["rt_processed"].append(test_result["nonce"])
            try:
                outcome = ds.save_browser_assessment(test_result, astronaut_id, mission_id,
                                                     task_context=task_context)
            except (DatabaseError, ValueError, KeyError, TypeError) as error:
                st.error(f"Assessment not saved: {error}")
            else:
                flash("success", f"Assessment {outcome['assessment_id']} saved. Re-evaluated status: "
                                 f"{outcome['analysis']['status_label']}.")
                st.rerun()

# ---------------------------------------------------------------------------
# Actigraphy
# ---------------------------------------------------------------------------

with tab_acti:
    st.subheader("Wrist actigraphy (Actiwatch-compatible) · recorded data")
    st.caption("Stored recordings imported from files or generated for the demo, not a live feed. "
               "AstroGuard has no live connection to any wristwatch and cannot send it commands.")
    recordings = ag.get_recordings(astronaut_id, mission_id=mission_id)
    main_sessions = [s for s in sessions if s["sleep_type"] == "main"]
    if not recordings:
        st.info("No actigraphy recording for this profile. Real Actiwatch recordings from the "
                "public research dataset can be imported below; they appear as separate "
                "research participant profiles (RES-…), never as astronauts.")
    for rec in recordings:
        if not rec["is_synthetic"]:
            if "4961281" in (rec["attribution"] or ""):
                source_html = (
                    f"<b>Dataset:</b> <a href='{ag.ZENODO_URL}' target='_blank' "
                    f"style='color:#56DDF2'>{ag.ZENODO_TITLE}</a> (Zenodo record 4961281 · "
                    f"<a href='{ag.ZENODO_DOI_URL}' target='_blank' style='color:#56DDF2'>"
                    "doi:10.5061/dryad.b8gtht7bh</a> · CC0 1.0)<br>"
                    "<b>Research source:</b> Angelova, Kusmakar, Karmakar, Zhu, Shelyag, Drummond "
                    "&amp; Ellis (2021): seven nights of wrist actigraphy from people with chronic "
                    "insomnia and their bed partners.<br>"
                    "<b>Device:</b> Respironics Actiwatch Spectrum Pro with Actiware software (as "
                    "stated by the dataset authors).<br>"
                    "<b>Participants:</b> research participants on Earth, <b>not astronauts</b>. "
                    "The dataset contains no reaction-time, cortisol or alertness outcomes, so "
                    "AstroGuard draws no alertness conclusion for this profile.<br>"
                    "<span style='color:#A9BBD3'>All times are shown in UTC. The Zenodo files were "
                    "recorded in Australian local time (UTC+10:00 or UTC+11:00, as stated in "
                    "each file's header), so a night starting about 13:00–14:00 UTC began "
                    "around midnight local time.</span>")
            else:
                source_html = ("<b>Source:</b> " + html_escape(rec["attribution"]
                                                               or "User-uploaded Actiware export")
                               + "<br><b>Participant:</b> imported file; not presented as an "
                                 "astronaut.")
            badge, color = "REAL WEARABLE DATA · research participant on Earth", "#68F0C0"
        else:
            source_html = ("<b>Source:</b> AstroGuard synthetic Actiwatch-like generator "
                           "(fictional data consistent with this demo astronaut's synthetic sleep "
                           "records). <b>Not real measurements and not from a real watch.</b>")
            badge, color = "SYNTHETIC DEMO DATA · fictional astronaut", "#FFB547"
        st.html(
            f'<div style="background:#13233D; border:1px solid #3A5A85; border-left:6px solid '
            f'{color}; border-radius:12px; padding:12px 16px; margin:4px 0 10px; color:#DCE7F5; '
            f'font-size:14px; line-height:1.6;"><b style="color:{color};">⌚ {badge}</b><br>'
            f'{source_html}</div>')
        summary = ag.recording_summary(rec["recording_id"])
        missing = summary["missing_activity"]
        signals = [name for name, present in (
            ("activity", summary["epochs"] > missing),
            ("light", summary["light_epochs"] > 0),
            ("device sleep/wake", summary["device_scored"] > 0)) if present]
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Recording period (UTC)",
                  f"{fmt_utc(rec['start_utc'])[:11]} → {fmt_utc(rec['end_utc'])[:11]}")
        r2.metric("Measurement interval", f"{rec['epoch_seconds']} s epochs",
                  help=f"{rec['epoch_count']:,} epochs in total.")
        r3.metric("Missing / off-wrist epochs", f"{missing / max(summary['epochs'], 1):.1%}",
                  help=f"{missing:,} epochs without activity data; "
                       f"{summary['off_wrist']:,} flagged off-wrist.")
        r4.metric("Signals present", ", ".join(signals) or "none")

    with st.expander("What each measurement means"):
        st.markdown("""
- **Activity count:** how much the wrist moved during one epoch (here 60 s), in device units.
  Long periods of low counts suggest rest or sleep; they do not prove sleep.
- **White light (lux):** ambient light at the wrist, useful context for daylight exposure and
  circadian timing.
- **Sleep/wake (device-scored):** the Actiware software's estimate for each epoch, derived from
  movement. Actigraphy cannot see EEG sleep stages (REM/deep).
- **Off-wrist / missing:** epochs where the watch was not worn or recorded no data. AstroGuard
  never fills these gaps; low coverage lowers the sensor-quality rating.
- **Rest interval (time in bed):** the period the device software marked as rest. Time in bed is
  not the same as sleep.

Actiwatch does **not** measure EEG sleep stages, cortisol, reaction time or heart-rate
variability. Reaction time comes from AstroGuard's separate PVT-inspired test.
""")

    if recordings and main_sessions:
        pick = st.selectbox("Night", list(range(len(main_sessions)))[::-1],
                            format_func=lambda i: f"{fmt_utc(main_sessions[i]['bedtime_utc'])} → "
                                                  f"{fmt_utc(main_sessions[i]['wake_time_utc'])} "
                                                  f"({main_sessions[i]['data_origin']})",
                            key=f"acti_night_{astronaut_id}_{mission_id}")
        night = main_sessions[pick]
        start = (parse_utc(night["bedtime_utc"]) - timedelta(hours=3)).strftime(sa.UTC_FORMAT)
        end = (parse_utc(night["wake_time_utc"]) + timedelta(hours=3)).strftime(sa.UTC_FORMAT)
        epochs = ag.get_epochs(astronaut_id, mission_id, start, end)
        if epochs:
            ef = pd.DataFrame(epochs)
            ef["time"] = pd.to_datetime(ef["epoch_start_utc"], utc=True)
            fig = go.Figure()
            fig.add_vrect(x0=night["bedtime_utc"], x1=night["wake_time_utc"], fillcolor="#56DDF2",
                          opacity=0.08, line_width=0, annotation_text="time in bed (sleep record)")
            fig.add_bar(x=ef["time"], y=ef["activity_count"], name="Activity (counts/epoch)",
                        marker_color="#B49AFF")
            fig.add_scatter(x=ef["time"], y=ef["white_light_lux"], name="White light (lux)",
                            yaxis="y2", line=dict(color="#FFD166", width=1))
            asleep = ef[ef["device_sleep_wake"] == "sleep"]
            if not asleep.empty:
                fig.add_scatter(x=asleep["time"], y=[-8] * len(asleep), mode="markers",
                                name="Device-scored sleep epoch",
                                marker=dict(symbol="square", size=4, color="#56DDF2"))
            gaps = ef[(ef["off_wrist"] == 1) | ef["activity_count"].isna()]
            if not gaps.empty:
                fig.add_scatter(x=gaps["time"], y=[-16] * len(gaps), mode="markers",
                                name="Off-wrist / missing epoch",
                                marker=dict(symbol="x", size=5, color="#FF6B6B"))
            fig.update_layout(yaxis2=dict(overlaying="y", side="right", title="lux",
                                          showgrid=False), yaxis=dict(title="activity counts"))
            show_chart(dark(fig, "Recorded activity and light around the selected night "
                                 "(historical)", height=400))
            in_bed = [e for e in epochs if night["bedtime_utc"] <= e["epoch_start_utc"]
                      < night["wake_time_utc"]]
            seconds = epochs[0]["epoch_seconds"]
            off = sum(1 for e in in_bed if e["off_wrist"] == 1 or e["activity_count"] is None)
            h1, h2, h3, h4 = st.columns(4)
            h1.metric("Time in bed", f"{night['time_in_bed_minutes'] / 60:.1f} h")
            h2.metric("Stored sleep estimate", "n/a" if night["estimated_sleep_minutes"] is None
                      else f"{night['estimated_sleep_minutes'] / 60:.1f} h",
                      help="Real data: epochs the device scored as sleep within the rest "
                           "interval. Demo data: the synthetic scenario value.")
            if seconds in ag.SCORING_WEIGHTS and in_bed:
                scores = ag.score_sleep_wake([e["activity_count"] for e in in_bed], seconds)
                heuristic = sum(1 for s in scores if s == "sleep") * seconds / 60
                h3.metric("AstroGuard heuristic cross-check", f"{heuristic / 60:.1f} h",
                          help="Weighted activity window vs a 40-count threshold, after published "
                               "descriptions of the Actiware algorithm. Illustrative, not "
                               "validated, not the vendor implementation.")
            h4.metric("Off-wrist / missing in bed", f"{off} of {len(in_bed)} epochs")
            if asleep.empty:
                st.caption("No device sleep/wake scores in this recording (synthetic recordings "
                           "carry none); only the heuristic cross-check is shown.")
        else:
            st.info("No actigraphy epochs around this night (e.g. a manual-only record).")

    research_present = any(ds.get_profile_kind(a) == "research" for a in labels)
    with st.expander("Import real Actiwatch data (Actiware CSV export)",
                     expanded=not research_present):
        st.markdown("Imported files are stored under a separate **research participant** "
                    "profile (`RES-<subject code>`), never under a fictional astronaut. Personal "
                    "header fields (name, gender, file paths, serial numbers) are not stored. "
                    f"Public dataset: [{ag.ZENODO_TITLE} (Zenodo 4961281)]({ag.ZENODO_URL}).")
        if ZENODO_ARCHIVE.exists():
            if st.button("Import 2 participants from the local Zenodo 4961281 archive",
                         key="zenodo_import", type="primary"):
                try:
                    items = ag.import_zenodo_archive(ZENODO_ARCHIVE, limit=2)
                except (DatabaseError, ValueError, OSError) as error:
                    st.error(f"Import failed: {error}")
                else:
                    st.session_state["pending_select"] = items[0]["astronaut_id"]
                    flash("success", "Imported real research participants: " + ", ".join(
                        i["astronaut_id"] for i in items) + " (people on Earth, not "
                        "astronauts). Now showing " + items[0]["astronaut_id"] + ".")
                    st.rerun()
        else:
            st.caption("Public dataset not found locally (expected at "
                       "data/external/zenodo_4961281/). See README for download instructions.")
        upload = st.file_uploader("Or upload an Actiware export (.csv)", type=["csv"],
                                  key="acti_upload")
        if upload is not None and st.button("Import uploaded file", key="acti_import"):
            try:
                info = ag.import_actiware_file(upload.getvalue(), source_name=upload.name,
                                               attribution="User-uploaded Actiware export")
            except (DatabaseError, ValueError) as error:
                st.error(f"Import failed: {error}")
            else:
                st.session_state["pending_select"] = info["astronaut_id"]
                flash("success", f"Imported {info['epochs']} epochs and "
                                 f"{info['sleep_periods']} sleep period(s) for "
                                 f"{info['astronaut_id']}.")
                st.rerun()
        st.caption(ag.ZENODO_ATTRIBUTION)

# ---------------------------------------------------------------------------
# Cortisol
# ---------------------------------------------------------------------------

with tab_cortisol:
    st.subheader("Optional cortisol observations (context only)")
    st.caption("Cortisol varies with time since waking, time of day, sampling method and assay. "
               "AstroGuard never uses it as a standalone alertness indicator; it does not affect "
               "the status.")
    if not cortisol:
        st.info("No cortisol observations for this astronaut and mission.")
    else:
        cf = pd.DataFrame(cortisol)
        fig = go.Figure()
        for unit, part in cf.groupby("unit"):
            fig.add_scatter(x=pd.to_datetime(part["sample_utc"], utc=True), y=part["value"],
                            mode="markers+lines", name=f"{unit}",
                            text=[f"{m:.0f} min after waking" if pd.notna(m) else "time since "
                                  "waking unknown" for m in part["minutes_since_awakening"]])
        fig.update_yaxes(title="value (unit as recorded; no conversion)")
        show_chart(dark(fig, "Cortisol samples (each unit plotted separately)"))
        st.dataframe(cf[["sample_utc", "value", "unit", "sampling_method",
                         "minutes_since_awakening", "data_origin", "is_synthetic", "notes"]],
                     width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Legacy health monitoring (original dashboard, unchanged logic)
# ---------------------------------------------------------------------------

METRIC_NAMES = {"heart_rate": "Heart Rate", "sleep_duration": "Sleep Hours"}


def get_week(measurements, astronaut_id):
    selected = measurements.loc[
        measurements["astronaut_id"].eq(astronaut_id)
        & measurements["metric_type"].isin(METRIC_NAMES)
    ].copy()
    if selected.empty:
        return pd.DataFrame(columns=["Day", *METRIC_NAMES.values()])

    selected["Day"] = selected["timestamp_utc"].dt.floor("D")
    # If a day has several readings for one metric, use its latest reading.
    selected = selected.sort_values(["timestamp_utc", "measurement_id"]).drop_duplicates(
        subset=["Day", "metric_type"], keep="last"
    )
    daily = (
        selected.pivot(index="Day", columns="metric_type", values="value")
        .reindex(columns=list(METRIC_NAMES))
        .rename(columns=METRIC_NAMES)
    )
    days = pd.date_range(end=daily.index.max(), periods=7, freq="D")
    return daily.reindex(days).rename_axis("Day").reset_index()


def create_health_chart(data, metric, title, color, unit):
    fig = go.Figure(go.Scatter(
        x=data["Day"], y=data[metric], mode="lines+markers",
        line=dict(color=color, width=3), marker=dict(size=9, color=color),
        fill="tozeroy", fillcolor="rgba(86, 221, 242, 0.05)", connectgaps=False,
        hovertemplate=f"%{{x|%d %b %Y}}<br>%{{y}} {unit}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=20, color="#F4F8FF")),
        paper_bgcolor="#172640", plot_bgcolor="#172640", font=dict(color="#B8CBE4"),
        height=360, margin=dict(l=35, r=35, t=70, b=45),
        xaxis=dict(type="date", showgrid=False, tickformat="%d %b", dtick=86400000),
        yaxis=dict(title=unit, gridcolor="#29476A", zeroline=False, rangemode="tozero"),
        showlegend=False,
    )
    return fig


def render_legacy_health():
    legacy_missions = [m["mission_id"] for m in get_missions(astronaut_id)]
    if mission_id not in legacy_missions:
        st.info("No legacy health measurements for this astronaut and mission. Legacy charts are "
                "available for the original demo crew (AST-001 to AST-003, MISSION-DEMO-01).")
        return
    rows = [row for metric_type in METRIC_NAMES for row in get_measurements(
        astronaut_id, metric_type=metric_type, mission_id=mission_id, limit=1000)]
    if not rows:
        st.warning("There are no heart rate or sleep readings for this astronaut.")
        return
    measurements = pd.DataFrame(rows)
    measurements["timestamp_utc"] = pd.to_datetime(
        measurements["timestamp_utc"], utc=True, errors="coerce", format="ISO8601")
    measurements["value"] = pd.to_numeric(measurements["value"], errors="coerce")
    measurements = measurements.dropna(subset=["timestamp_utc", "value"])
    health_data = get_week(measurements, astronaut_id)
    if health_data.empty:
        st.warning("There are no heart rate or sleep readings for this astronaut.")
        return

    st.header("Health Trends")
    chart1, chart2 = st.columns(2)
    with chart1:
        show_chart(create_health_chart(health_data, "Heart Rate", "Heart Rate Trend",
                                       "#56DDF2", "bpm"))
    with chart2:
        show_chart(create_health_chart(health_data, "Sleep Hours", "Sleep Duration Trend",
                                       "#B49AFF", "hours"))
    first_day, last_day = health_data["Day"].iloc[0], health_data["Day"].iloc[-1]
    st.caption(f"Astronaut health measurements · {first_day:%d %b %Y} to "
               f"{last_day:%d %b %Y} (UTC).")

    st.header("Health Intelligence & Alerts")
    try:
        report = analyze_astronaut_range(astronaut_id, mission_id, first_day.date().isoformat(),
                                         last_day.date().isoformat(), aggregation="latest")
    except (DatabaseError, ValueError) as error:
        st.error(f"Could not analyze the selected measurements: {error}")
        return
    results = report["results"]
    warnings = [r for r in results if r["alert_level"] == "warning"]
    incomplete = [r for r in results if r["data_quality_issues"]]
    for r in warnings:
        st.warning(f"{r['date']} · Potential Health Anomaly Detected")
        for anomaly in r["detected_anomalies"]:
            st.write(anomaly["explanation"])
        if r["recommendations"]:
            st.markdown("**Recommended action**")
            for recommendation in r["recommendations"]:
                st.write(recommendation)
    if incomplete:
        st.info("Some measurements in this window could not be fully assessed.")
        with st.expander("See missing measurements or history"):
            for r in incomplete:
                st.write(r["date"], r["data_quality_issues"])
    if results and not warnings and not incomplete:
        st.success("No statistical anomaly detected in the selected window.")
    elif not results:
        st.info("No analysis results for the selected window.")
    st.caption("Statistical screening for demonstration purposes.")


with tab_legacy:
    try:
        render_legacy_health()
    except DatabaseError:
        st.error("Could not load missions or measurements from the database.")

st.divider()
st.html("""
<div style="text-align:center; color:#91A7C4; padding:16px; font-size:13px;">
    AstroGuard | NASA Space Apps Hackathon | Research prototype - not a medical device,
    not clinically validated, never interrupts astronaut tasks. Demo astronauts are fictional.
</div>
""")
