import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from backend.frontend_service import (
    analyze_astronaut_range,
    get_astronauts,
    get_measurements,
    get_missions,
)


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
[data-testid="stAlert"] p { color: #24334a !important; }
[data-testid="stCaptionContainer"] p { color: #91A7C4 !important; }
hr { border-color: #29476A !important; }
</style>
""", unsafe_allow_html=True)
st.html("""
<div style="padding:34px; background:linear-gradient(120deg,#142743,#0C1830); border:1px solid #29476A; border-radius:20px; margin-bottom:32px;">
    <div style="color:#56DDF2; font-size:13px; letter-spacing:3px; font-weight:700;">MISSION CONTROL / HEALTH INTELLIGENCE</div>
    <h1 style="font-size:48px; margin:16px 0; color:#FFFFFF;">🚀 AstroGuard</h1>
    <p style="font-size:19px; color:#B8CBE4;">Astronaut Health Monitoring</p>
    <div style="display:inline-block; background:#123B36; color:#68F0C0; padding:10px 18px; border-radius:24px; font-size:14px; margin-top:12px;">● SYSTEM ONLINE · DEMO MODE</div>
</div>
""")

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

try:
    astronauts = get_astronauts()
except DatabaseError:
    st.error("Could not load astronauts. Check that the mission database is initialized.")
    st.stop()
if not astronauts:
    st.warning("No astronauts found. Import the mission data before opening the dashboard.")
    st.stop()

astronaut_names = {
    row["astronaut_id"]: row["display_name"] or row["astronaut_id"]
    for row in astronauts
}
astronaut_id = st.selectbox(
    "Select astronaut", list(astronaut_names), format_func=astronaut_names.__getitem__
)

try:
    missions = get_missions(astronaut_id)
    if not missions:
        st.info("No missions found for this astronaut.")
        st.stop()

    mission_id = st.selectbox(
        "Select mission", [mission["mission_id"] for mission in missions]
    )
    rows = [
        row
        for metric_type in METRIC_NAMES
        for row in get_measurements(
            astronaut_id, metric_type=metric_type, mission_id=mission_id, limit=1000
        )
    ]
except DatabaseError:
    st.error("Could not load missions or measurements from the database.")
    st.stop()

if not rows:
    st.warning("There are no heart rate or sleep readings for this astronaut.")
    st.stop()

measurements = pd.DataFrame(rows)
measurements["timestamp_utc"] = pd.to_datetime(
    measurements["timestamp_utc"], utc=True, errors="coerce", format="ISO8601"
)
measurements["value"] = pd.to_numeric(measurements["value"], errors="coerce")
measurements = measurements.dropna(subset=["timestamp_utc", "value"])
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
    f"Astronaut health measurements · {first_day:%d %b %Y} to "
    f"{last_day:%d %b %Y} (UTC)."
)

st.divider()
st.header("Health Intelligence & Alerts")

try:
    report = analyze_astronaut_range(
        astronaut_id,
        mission_id,
        first_day.date().isoformat(),
        last_day.date().isoformat(),
        aggregation="latest",
    )
except (DatabaseError, ValueError) as error:
    st.error(f"Could not analyze the selected measurements: {error}")
else:
    results = report["results"]
    warnings = [result for result in results if result["alert_level"] == "warning"]
    incomplete = [result for result in results if result["data_quality_issues"]]

    for result in warnings:
        st.warning(f"{result['date']} · Potential Health Anomaly Detected")
        for anomaly in result["detected_anomalies"]:
            st.write(anomaly["explanation"])
        if result["recommendations"]:
            st.markdown("**Recommended action**")
            for recommendation in result["recommendations"]:
                st.write(recommendation)
    if incomplete:
        st.info("Some measurements in this window could not be fully assessed.")
        with st.expander("See missing measurements or history"):
            for result in incomplete:
                st.write(result["date"], result["data_quality_issues"])

    if results and not warnings and not incomplete:
        st.success("No statistical anomaly detected in the selected window.")
    elif not results:
        st.info("No analysis results for the selected window.")

st.caption("Statistical screening for demonstration purposes.")
st.divider()
st.html("""
<div style="text-align:center; color:#91A7C4; padding:20px; font-size:14px;">
    AstroGuard | NASA Space Apps Hackathon | Mission Control Demo Prototype
</div>
""")    