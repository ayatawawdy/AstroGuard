
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from html import escape
import textwrap


def render_html(html):
    import textwrap
    cleaned = textwrap.dedent(html).strip()
    st.html(cleaned)

# -----------------------------------
# PAGE CONFIGURATION
# -----------------------------------

st.set_page_config(
    page_title="AstroGuard | Mission Control",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# -----------------------------------
# NASA DARK THEME
# -----------------------------------

st.markdown("""
<style>
.stApp {
    background: linear-gradient(135deg, #080F1F 0%, #101D35 55%, #071326 100%);
    color: #EAF4FF;
}

.block-container {
    padding-top: 2rem;
    padding-bottom: 2rem;
    max-width: 1500px;
}

h1, h2, h3 {
    color: #F4F8FF !important;
    font-family: 'Segoe UI', sans-serif;
}

p {
    color: #C6D6EB;
}

[data-testid="stMetric"] {
    background: #172640;
    border: 1px solid #29476A;
    border-radius: 16px;
    padding: 22px;
}

[data-testid="stMetricLabel"] p {
    color: #B8CBE4 !important;
}

[data-testid="stMetricValue"] {
    color: #56DDF2;
}

[data-testid="stCaptionContainer"] p {
    color: #91A7C4 !important;
}

hr {
    border-color: #29476A !important;
}
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
    <p style="font-size:19px; color:#B8CBE4;">AI-Assisted Astronaut Health Monitoring</p>
    <div style="display:inline-block; background:#123B36; color:#68F0C0; padding:10px 18px; border-radius:24px; font-size:14px; margin-top:12px;">● SYSTEM ONLINE · DEMO MODE</div>
</div>
""",
    unsafe_allow_html=True
)


# -----------------------------------
# HEALTH TRENDS
# -----------------------------------

st.divider()
st.header("Health Trends")

def create_health_chart(values, title, color, unit):
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=health_data["Day"],
        y=values,
        mode="lines+markers",
        line=dict(color=color, width=3),
        marker=dict(size=9, color=color),
        fill="tozeroy",
        fillcolor="rgba(86, 221, 242, 0.05)",
        hovertemplate=(
            f"%{{x}}<br>%{{y}} {unit}<extra></extra>"
        )
    ))

    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=20, color="#F4F8FF")
        ),
        paper_bgcolor="#172640",
        plot_bgcolor="#172640",
        font=dict(color="#B8CBE4"),
        height=360,
        margin=dict(l=35, r=35, t=70, b=45),
        xaxis=dict(
            showgrid=False,
            categoryorder="array",
            categoryarray=[
                "Mon", "Tue", "Wed", "Thu",
                "Fri", "Sat", "Sun"
            ]
        ),
        yaxis=dict(
            gridcolor="#29476A",
            zeroline=False,
            rangemode="tozero"
        ),
        showlegend=False
    )

    return fig

chart1, chart2 = st.columns(2)

with chart1:
    fig_heart = create_health_chart(
        health_data["Heart Rate"],
        "Heart Rate Trend",
        "#56DDF2",
        "bpm"
    )
    st.plotly_chart(
        fig_heart,
        use_container_width=True,
        config={"displaylogo": False}
    )

with chart2:
    fig_sleep = create_health_chart(
        health_data["Sleep Hours"],
        "Sleep Duration Trend",
        "#B49AFF",
        "hours"
    )
    st.plotly_chart(
        fig_sleep,
        use_container_width=True,
        config={"displaylogo": False}
    )

st.caption(
    "Interactive charts based on simulated astronaut health data."
)

# -----------------------------------
# HEALTH INTELLIGENCE AND ALERTS
# -----------------------------------

st.divider()
st.header("Health Intelligence & Alerts")

if anomaly_detected:

    alert_html = f"""
    <div style="
        background:#172640;
        border:1px solid #A66A43;
        border-left:5px solid #FFAA66;
        border-radius:16px;
        padding:26px;
        margin:20px 0;
    ">
        <div style="
            color:#FFB780;
            font-size:13px;
            letter-spacing:2px;
            font-weight:700;
        ">
            WARNING / HEALTH MONITORING
        </div>

        <h3 style="
            color:#FFFFFF;
            margin-top:14px;
        ">
            Potential Health Anomaly Detected
        </h3>

        <p style="color:#D4E0F0;font-size:16px;">
            Unusual combined changes were detected
            in the simulated health metrics
            over the last 7 days.
        </p>

        <div style="
            display:flex;
            gap:20px;
            flex-wrap:wrap;
            margin-top:24px;
        ">
            <div style="
                flex:1;
                min-width:180px;
                background:#101D35;
                padding:20px;
                border-radius:12px;
            ">
                <div style="color:#AFC6E5;">
                    HEART RATE CHANGE
                </div>

                <div style="
                    font-size:30px;
                    color:#FFB780;
                    font-weight:700;
                ">
                    +{heart_rate_change:.0f} bpm
                </div>
            </div>

            <div style="
                flex:1;
                min-width:180px;
                background:#101D35;
                padding:20px;
                border-radius:12px;
            ">
                <div style="color:#AFC6E5;">
                    SLEEP DURATION CHANGE
                </div>

                <div style="
                    font-size:30px;
                    color:#FFB780;
                    font-weight:700;
                ">
                    {sleep_change:.1f} hours
                </div>
            </div>
        </div>
    </div>
    """

    render_html(alert_html)

    render_html("""
    <div style="
        background:#142C40;
        border:1px solid #315B70;
        border-radius:16px;
        padding:24px;
        margin-bottom:20px;
    ">
        <div style="
            color:#56DDF2;
            font-weight:700;
            font-size:17px;
            margin-bottom:12px;
        ">
            RECOMMENDED ACTION
        </div>

        <p style="
            color:#D4E0F0;
            font-size:16px;
            margin:0;
        ">
            Review recent sleep patterns, workload,
            and physical activity. Consult the mission
            medical team to evaluate the observed changes.
        </p>
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

st.markdown("""
<div style="
    text-align:center;
    color:#91A7C4;
    padding:20px;
    font-size:14px;
">
    AstroGuard | NASA Space Apps Hackathon |
    Mission Control Demo Prototype
</div>
""", unsafe_allow_html=True)
