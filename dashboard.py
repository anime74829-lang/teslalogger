"""
dashboard.py
------------
Streamlit dashboard for TeslaLogger.

Run with:
    streamlit run dashboard.py

Or let main.py launch it for you.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

import database as db

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "teslalogger.db")

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="TeslaLogger",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("⚡ TeslaLogger")
    st.caption("Your personal Tesla data logger")
    auto_refresh = st.toggle("Auto-refresh (30 s)", value=True)
    if auto_refresh:
        st.write("")
        st.info("Dashboard refreshes every 30 seconds")

    st.divider()
    st.caption(f"DB: `{DB_PATH}`")

# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------

if auto_refresh:
    import time
    # Streamlit experimental rerun every 30 s via a hidden counter
    if "refresh_count" not in st.session_state:
        st.session_state.refresh_count = 0

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

latest = db.get_latest_snapshot(DB_PATH)
snapshots = db.get_recent_snapshots(DB_PATH, limit=1000)
drives = db.get_drives(DB_PATH)
charges = db.get_charges(DB_PATH)

df_snap = pd.DataFrame(snapshots) if snapshots else pd.DataFrame()
df_drives = pd.DataFrame(drives) if drives else pd.DataFrame()
df_charges = pd.DataFrame(charges) if charges else pd.DataFrame()

# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------

tab_live, tab_drives, tab_charges, tab_history = st.tabs(
    ["🚗 Live Status", "📍 Drives", "⚡ Charging", "📈 History"]
)

# ============================================================
# TAB 1 — Live Status
# ============================================================

with tab_live:
    if latest is None:
        st.warning("No data yet. Make sure the poller is running.")
        st.stop()

    # ── Header ──────────────────────────────────────────────────────────
    col_name, col_ts = st.columns([3, 1])
    with col_name:
        st.header(latest.get("display_name") or "My Tesla")
        st.caption(f"VIN: {latest.get('vin') or '—'}  •  "
                   f"Software: {latest.get('software_version') or '—'}")
    with col_ts:
        recorded = latest.get("recorded_at", "")
        st.metric("Last updated", recorded[11:19] if recorded else "—")

    st.divider()

    # ── Key metrics ──────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)

    bat = latest.get("battery_level")
    c1.metric(
        "🔋 Battery",
        f"{bat:.0f}%" if bat is not None else "—",
        delta=None,
    )

    rng = latest.get("battery_range")
    c2.metric("📏 Range", f"{rng:.0f} mi" if rng is not None else "—")

    spd = latest.get("speed")
    c3.metric("🚀 Speed", f"{spd:.0f} mph" if spd is not None else "Parked")

    pwr = latest.get("power")
    c4.metric("⚡ Power", f"{pwr:+.0f} kW" if pwr is not None else "—")

    odo = latest.get("odometer")
    c5.metric("🛞 Odometer", f"{odo:,.0f} mi" if odo is not None else "—")

    st.write("")
    c6, c7, c8 = st.columns(3)

    chg_state = latest.get("charging_state") or "Disconnected"
    c6.metric("🔌 Charging", chg_state)

    inside = latest.get("inside_temp")
    outside = latest.get("outside_temp")
    c7.metric(
        "🌡️ Temp (in/out)",
        f"{inside:.1f} °C  /  {outside:.1f} °C" if inside and outside else "—",
    )

    locked = latest.get("locked")
    c8.metric("🔒 Locked", "Yes" if locked else "No" if locked is not None else "—")

    # ── Battery gauge ────────────────────────────────────────────────────
    if bat is not None:
        st.write("")
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=bat,
            title={"text": "Battery %"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#00c04b" if bat > 20 else "#e05252"},
                "steps": [
                    {"range": [0, 20], "color": "#ffeaea"},
                    {"range": [20, 80], "color": "#f0fff0"},
                    {"range": [80, 100], "color": "#e0ffe0"},
                ],
                "threshold": {
                    "line": {"color": "orange", "width": 4},
                    "thickness": 0.75,
                    "value": latest.get("charge_limit_soc") or 80,
                },
            },
            number={"suffix": "%"},
        ))
        fig_gauge.update_layout(height=300, margin=dict(t=30, b=10))
        st.plotly_chart(fig_gauge, use_container_width=True)

    # ── Live map ─────────────────────────────────────────────────────────
    lat = latest.get("latitude")
    lon = latest.get("longitude")
    if lat and lon:
        st.subheader("📍 Current location")
        try:
            import folium
            from streamlit_folium import st_folium
            m = folium.Map(location=[lat, lon], zoom_start=14)
            folium.Marker(
                [lat, lon],
                popup=latest.get("display_name", "Tesla"),
                tooltip="Current position",
                icon=folium.Icon(color="red", icon="car", prefix="fa"),
            ).add_to(m)
            st_folium(m, width=None, height=350)
        except ImportError:
            st.map(pd.DataFrame({"lat": [lat], "lon": [lon]}))


# ============================================================
# TAB 2 — Drives
# ============================================================

with tab_drives:
    if df_drives.empty:
        st.info("No drives recorded yet.")
    else:
        # ── Summary cards ────────────────────────────────────────────────
        completed = df_drives.dropna(subset=["ended_at"])
        c1, c2, c3 = st.columns(3)
        c1.metric("Total drives", len(completed))
        total_mi = completed["distance_miles"].sum()
        c2.metric("Total distance", f"{total_mi:,.1f} mi")
        avg_dur = completed["duration_min"].mean()
        c3.metric("Avg duration", f"{avg_dur:.0f} min" if not pd.isna(avg_dur) else "—")

        st.divider()

        # ── Distance over time chart ─────────────────────────────────────
        if not completed.empty and "distance_miles" in completed.columns:
            completed = completed.copy()
            completed["started_at"] = pd.to_datetime(completed["started_at"])
            fig_dist = px.bar(
                completed.sort_values("started_at"),
                x="started_at", y="distance_miles",
                labels={"started_at": "Date", "distance_miles": "Distance (mi)"},
                title="Distance per Drive",
                color="distance_miles",
                color_continuous_scale="Teal",
            )
            fig_dist.update_layout(coloraxis_showscale=False)
            st.plotly_chart(fig_dist, use_container_width=True)

        # ── Drive table ───────────────────────────────────────────────────
        st.subheader("All drives")
        display_cols = [
            "started_at", "ended_at", "distance_miles",
            "duration_min", "max_speed", "start_range", "end_range",
        ]
        show_cols = [c for c in display_cols if c in df_drives.columns]
        st.dataframe(
            df_drives[show_cols].rename(columns={
                "started_at": "Start", "ended_at": "End",
                "distance_miles": "Distance (mi)", "duration_min": "Duration (min)",
                "max_speed": "Max speed (mph)", "start_range": "Start range (mi)",
                "end_range": "End range (mi)",
            }),
            use_container_width=True,
        )


# ============================================================
# TAB 3 — Charging
# ============================================================

with tab_charges:
    if df_charges.empty:
        st.info("No charging sessions recorded yet.")
    else:
        completed_c = df_charges.dropna(subset=["ended_at"])
        c1, c2, c3 = st.columns(3)
        c1.metric("Charging sessions", len(completed_c))
        total_kwh = completed_c["energy_added_kwh"].sum()
        c2.metric("Total energy added", f"{total_kwh:,.1f} kWh")
        total_miles = completed_c["miles_added"].sum()
        c3.metric("Total miles added", f"{total_miles:,.0f} mi")

        st.divider()

        # ── Energy per session chart ──────────────────────────────────────
        if not completed_c.empty:
            completed_c = completed_c.copy()
            completed_c["started_at"] = pd.to_datetime(completed_c["started_at"])
            fig_kwh = px.bar(
                completed_c.sort_values("started_at"),
                x="started_at", y="energy_added_kwh",
                labels={"started_at": "Date", "energy_added_kwh": "Energy (kWh)"},
                title="Energy Added per Charge Session",
                color="energy_added_kwh",
                color_continuous_scale="Electric",
            )
            fig_kwh.update_layout(coloraxis_showscale=False)
            st.plotly_chart(fig_kwh, use_container_width=True)

        # ── Charge table ──────────────────────────────────────────────────
        st.subheader("All charging sessions")
        display_cols = [
            "started_at", "ended_at", "start_battery_level", "end_battery_level",
            "energy_added_kwh", "miles_added", "duration_min", "max_charger_power",
        ]
        show_cols = [c for c in display_cols if c in df_charges.columns]
        st.dataframe(
            df_charges[show_cols].rename(columns={
                "started_at": "Start", "ended_at": "End",
                "start_battery_level": "Start %", "end_battery_level": "End %",
                "energy_added_kwh": "Energy (kWh)", "miles_added": "Miles added",
                "duration_min": "Duration (min)", "max_charger_power": "Max power (kW)",
            }),
            use_container_width=True,
        )


# ============================================================
# TAB 4 — History charts
# ============================================================

with tab_history:
    if df_snap.empty:
        st.info("No snapshot history yet.")
    else:
        df_snap["recorded_at"] = pd.to_datetime(df_snap["recorded_at"])
        df_snap = df_snap.sort_values("recorded_at")

        # ── Battery level over time ───────────────────────────────────────
        if "battery_level" in df_snap.columns:
            fig_bat = px.line(
                df_snap, x="recorded_at", y="battery_level",
                title="Battery Level Over Time",
                labels={"recorded_at": "Time", "battery_level": "Battery (%)"},
                color_discrete_sequence=["#00c04b"],
            )
            fig_bat.update_yaxes(range=[0, 100])
            st.plotly_chart(fig_bat, use_container_width=True)

        # ── Range over time ───────────────────────────────────────────────
        if "battery_range" in df_snap.columns:
            fig_range = px.line(
                df_snap, x="recorded_at", y="battery_range",
                title="Estimated Range Over Time",
                labels={"recorded_at": "Time", "battery_range": "Range (mi)"},
                color_discrete_sequence=["#3b82f6"],
            )
            st.plotly_chart(fig_range, use_container_width=True)

        # ── Power draw ───────────────────────────────────────────────────
        if "power" in df_snap.columns:
            df_power = df_snap[df_snap["power"].notna()].copy()
            if not df_power.empty:
                fig_pwr = px.area(
                    df_power, x="recorded_at", y="power",
                    title="Power Draw / Regen (kW)",
                    labels={"recorded_at": "Time", "power": "Power (kW)"},
                    color_discrete_sequence=["#f59e0b"],
                )
                fig_pwr.add_hline(y=0, line_dash="dash", line_color="gray")
                st.plotly_chart(fig_pwr, use_container_width=True)

        # ── Temperature ──────────────────────────────────────────────────
        if "inside_temp" in df_snap.columns and "outside_temp" in df_snap.columns:
            df_temp = df_snap[
                df_snap["inside_temp"].notna() & df_snap["outside_temp"].notna()
            ].copy()
            if not df_temp.empty:
                fig_temp = go.Figure()
                fig_temp.add_trace(go.Scatter(
                    x=df_temp["recorded_at"], y=df_temp["inside_temp"],
                    name="Inside", line=dict(color="#ef4444"),
                ))
                fig_temp.add_trace(go.Scatter(
                    x=df_temp["recorded_at"], y=df_temp["outside_temp"],
                    name="Outside", line=dict(color="#6366f1"),
                ))
                fig_temp.update_layout(
                    title="Temperature Over Time (°C)",
                    xaxis_title="Time", yaxis_title="°C",
                )
                st.plotly_chart(fig_temp, use_container_width=True)

# ---------------------------------------------------------------------------
# Footer auto-refresh trigger
# ---------------------------------------------------------------------------

if auto_refresh:
    time.sleep(30)
    st.rerun()
