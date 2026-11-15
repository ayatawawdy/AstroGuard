from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


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
.block-container { padding: 2rem 1rem; max-width: 1500px; }
h1, h2, h3 { color: #F4F8FF !important; font-family: 'Segoe UI', sans-serif; }
p { color: #C6D6EB; }
[data-testid="stCaptionContainer"] p { color: #91A7C4 !important; }
hr { border-color: #29476A !important; }
</style>
""", unsafe_allow_html=True)

# -----------------------------------
# DEMONSTRATION DATA
# -----------------------------------


raw_data = pd.read_csv("data/synthetic/health_measurements.csv")

raw_data["timestamp_utc"] = pd.to_datetime(
    raw_data["timestamp_utc"], utc=True
)

astronaut_id = raw_data["astronaut_id"].iloc[0]
astronaut_data = raw_data[
    raw_data["astronaut_id"] == astronaut_id
]

health_data = astronaut_data.pivot_table(
    index="timestamp_utc",
    columns="metric_type",
    values="value",
    aggfunc="mean"
).sort_index().tail(7).reset_index()

health_data = health_data.rename(columns={
    "heart_rate": "Heart Rate",
    "sleep_duration": "Sleep Hours"
})

health_data["Day"] = health_data["timestamp_utc"].dt.strftime("%a")
health_data["Activity"] = 0


latest = health_data.iloc[-1]

heart_rate_change = (
    health_data["Heart Rate"].iloc[-1]
    - health_data["Heart Rate"].iloc[0]
)

sleep_change = (
    health_data["Sleep Hours"].iloc[-1]
    - health_data["Sleep Hours"].iloc[0]
)

# Demo rules only, not clinical thresholds
anomaly_detected = (
    heart_rate_change >= 10
    and sleep_change <= -2
)

# -----------------------------------
# HEADER
# -----------------------------------


# -----------------------------------
# HEADER
# -----------------------------------

st.markdown(
    """
<div style="padding:34px; background:linear-gradient(120deg,#142743,#0C1830); border:1px solid #29476A; border-radius:20px; margin-bottom:32px;">
    <div style="color:#56DDF2; font-size:13px; letter-spacing:3px; font-weight:700;">MISSION CONTROL / HEALTH INTELLIGENCE</div>
    <h1 style="font-size:48px; margin:16px 0; color:#FFFFFF;">🚀 AstroGuard</h1>
    <p style="font-size:19px; color:#B8CBE4;">Astronaut Health Monitoring</p>
    <div style="display:inline-block; background:#123B36; color:#68F0C0; padding:10px 18px; border-radius:24px; font-size:14px; margin-top:12px;">● SYSTEM ONLINE · DEMO MODE</div>
</div>
""")
DATA_FILE = Path(__file__).resolve().parent / "health_measurements_clean.csv"
METRIC_NAMES = {"heart_rate": "Heart Rate", "sleep_duration": "Sleep Hours"}


def load_measurements(path):
    measurements = pd.read_csv(path, dtype={"astronaut_id": "string"})
    required = {"astronaut_id", "metric_type", "timestamp_utc", "value"}
    missing = required.difference(measurements.columns)
    if missing:
        raise ValueError("Missing CSV columns: " + ", ".join(sorted(missing)))

    measurements["timestamp_utc"] = pd.to_datetime(
        measurements["timestamp_utc"], utc=True, errors="coerce"
    )
    measurements["value"] = pd.to_numeric(measurements["value"], errors="coerce")
    return measurements.dropna(subset=["astronaut_id", "timestamp_utc", "value"])

def get_week(measurements, astronaut_id):
    selected = measurements.loc[
        measurements["astronaut_id"].eq(astronaut_id)
        & measurements["metric_type"].isin(METRIC_NAMES)
    ].copy()
    if selected.empty:
        return pd.DataFrame(columns=["Day", *METRIC_NAMES.values()])

    selected["Day"] = selected["timestamp_utc"].dt.floor("D")
    # If a day has several readings for one metric, use its latest reading.
    selected = selected.sort_values("timestamp_utc").drop_duplicates(
        subset=["Day", "metric_type"], keep="last"
    )
    daily = (
        selected.pivot(index="Day", columns="metric_type", values="value")
        .reindex(columns=list(METRIC_NAMES))
        .rename(columns=METRIC_NAMES)
    )
    days = pd.date_range(end=daily.index.max(), periods=7, freq="D")
    return daily.reindex(days).rename_axis("Day").reset_index()

try:
    measurements = load_measurements(DATA_FILE)
except FileNotFoundError:
    st.error("Place health_measurements_clean.csv next to app.py.")
    st.stop()
except (OSError, ValueError) as error:
    st.error(f"Could not read the measurements CSV: {error}")
    st.stop()

astronauts = sorted(measurements["astronaut_id"].unique())
if not astronauts:
    st.warning("The measurements CSV has no usable rows.")
    st.stop()

astronaut_id = st.selectbox("Select astronaut", astronauts)
health_data = get_week(measurements, astronaut_id)
if health_data.empty:
    st.warning("There are no heart rate or sleep readings for this astronaut.")
    st.stop()

def create_health_chart(data, metric, title, color, unit):
    fig = go.Figure(go.Scatter(
        x=data["Day"],
        y=data[metric],
        mode="lines+markers",
        line=dict(color=color, width=3),
        marker=dict(size=9, color=color),
        fill="tozeroy",
        fillcolor="rgba(86, 221, 242, 0.05)",
        connectgaps=False,
        hovertemplate=f"%{{x|%d %b %Y}}<br>%{{y}} {unit}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=20, color="#F4F8FF")),
        paper_bgcolor="#172640",
        plot_bgcolor="#172640",
        font=dict(color="#B8CBE4"),
        height=360,
        margin=dict(l=35, r=35, t=70, b=45),
        xaxis=dict(type="date", showgrid=False, tickformat="%d %b", dtick=86400000),
        yaxis=dict(title=unit, gridcolor="#29476A", zeroline=False, rangemode="tozero"),
        showlegend=False,
    )
    return fig

st.divider()
st.header("Health Trends")
chart1, chart2 = st.columns(2)

with chart1:
    st.plotly_chart(
        create_health_chart(health_data, "Heart Rate", "Heart Rate Trend", "#56DDF2", "bpm"),
        width="stretch",
        config={"displaylogo": False},
    )

with chart2:
    st.plotly_chart(
        create_health_chart(health_data, "Sleep Hours", "Sleep Duration Trend", "#B49AFF", "hours"),
        width="stretch",
        config={"displaylogo": False},
    )

first_day = health_data["Day"].iloc[0]
last_day = health_data["Day"].iloc[-1]
st.caption(
    f"Simulated astronaut health data · {first_day:%d %b %Y} to "
    f"{last_day:%d %b %Y} (UTC)."
)
st.divider()
st.header("Health Intelligence & Alerts")

heart_rate_change = health_data["Heart Rate"].iloc[-1] - health_data["Heart Rate"].iloc[0]
sleep_change = health_data["Sleep Hours"].iloc[-1] - health_data["Sleep Hours"].iloc[0]
can_assess = pd.notna(heart_rate_change) and pd.notna(sleep_change)

# Use the existing demonstration rule on the first and last days of the window.
anomaly_detected = can_assess and heart_rate_change >= 10 and sleep_change <= -2

if not can_assess:
    st.info("The demo rule needs both measurements on the first and last days of the window.")
elif anomaly_detected:
    st.html(f"""
    <div style="background:#172640; border:1px solid #A66A43; border-left:5px solid #FFAA66;
                border-radius:16px; padding:26px; margin:20px 0;">
        <div style="color:#FFB780; font-size:13px; letter-spacing:2px; font-weight:700;">
            WARNING / HEALTH MONITORING
        </div>
        <h3>Potential Health Anomaly Detected</h3>
        <p>Combined changes met the demo rule between {first_day:%d %b} and {last_day:%d %b}.</p>
        <div style="display:flex; gap:20px; flex-wrap:wrap; margin-top:24px;">
            <div style="flex:1; min-width:180px; background:#101D35; padding:20px; border-radius:12px;">
                <div style="color:#AFC6E5;">HEART RATE CHANGE</div>
                <div style="font-size:30px; color:#FFB780; font-weight:700;">{heart_rate_change:+.0f} bpm</div>
            </div>
            <div style="flex:1; min-width:180px; background:#101D35; padding:20px; border-radius:12px;">
                <div style="color:#AFC6E5;">SLEEP DURATION CHANGE</div>
                <div style="font-size:30px; color:#FFB780; font-weight:700;">{sleep_change:+.1f} hours</div>
              </div>
        </div>
    </div>
    """)
    st.html("""
    <div style="background:#142C40; border:1px solid #315B70; border-radius:16px; padding:24px;">
        <div style="color:#56DDF2; font-size:17px; font-weight:700;">RECOMMENDED ACTION</div>
        <p>Review recent sleep patterns, workload, and physical activity.
        Consult the mission medical team to evaluate the observed changes.</p>
    </div>
    """)
else:
    st.html(f"""
    <div style="background:#123B36; border:1px solid #2D8A75; border-radius:16px; padding:24px; margin:20px 0;">
        <h3>No Anomaly Detected</h3>
        <p>The changes in this window did not meet the combined demo rule.</p>
        <p>Heart rate: {heart_rate_change:+.0f} bpm · Sleep: {sleep_change:+.1f} hours</p>
    </div>
    """)

else:

   
   st.markdown("""
    <div style="background:#123B36; border:1px solid #2D8A75; border-radius:16px; padding:24px; margin:20px 0;">
    <h3 style="color:#68F0C0;">No Anomaly Detected</h3>
    <p style="color:#D4E0F0;">No unusual combined changes were identified by the current demonstration rules.</p>
    </div>
     """, unsafe_allow_html=True)


st.caption(
    "SIMULATED DATA | RULE-BASED DETECTION | "
    "NOT A VALIDATED MEDICAL ALERT OR AI DIAGNOSIS"
)

# -----------------------------------
# FOOTER
# -----------------------------------

st.divider()
st.html("""
<div style="text-align:center; color:#91A7C4; padding:20px; font-size:14px;">
    AstroGuard | NASA Space Apps Hackathon | Mission Control Demo Prototype
</div>
""")

        