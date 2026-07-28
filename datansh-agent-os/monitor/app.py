from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
EVENTS_PATH = ROOT / "monitor" / "events.jsonl"

STATUS_COLORS = {
    "idle": "#6b7280",
    "queued": "#6b7280",
    "running": "#2563eb",
    "success": "#16a34a",
    "error": "#dc2626",
    "warning": "#d97706",
}


def load_events() -> list[dict]:
    if not EVENTS_PATH.exists():
        return []
    events = []
    for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def latest_run(events: list[dict]) -> str | None:
    if not events:
        return None
    return sorted({event["run_id"] for event in events})[-1]


def rel_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if path.is_absolute():
        return path
    return ROOT / path


def status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#6b7280")
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:999px;font-size:12px">{status}</span>'


st.set_page_config(page_title="Datansh Mission Control", layout="wide")
st.title("Datansh Mission Control")

events = load_events()
if not events:
    st.info("No events yet. Run the demo to populate monitor/events.jsonl.")
    st.stop()

run_options = sorted({event["run_id"] for event in events}, reverse=True)
selected_run = st.selectbox("Run", run_options, index=0)
run_events = [event for event in events if event["run_id"] == selected_run]

started = run_events[0].get("timestamp")
ended = run_events[-1].get("timestamp")
errors = [event for event in run_events if event.get("status") == "error" or event.get("event_type") == "error"]
agents = {}
for event in run_events:
    agents[event.get("agent", "Unknown")] = event

completed = sum(1 for event in agents.values() if event.get("status") == "success")
cols = st.columns(4)
cols[0].metric("Run ID", selected_run)
cols[1].metric("Agents Complete", completed)
cols[2].metric("Errors", len(errors))
cols[3].metric("Events", len(run_events))

st.subheader("Agent Status")
rows = []
for agent, event in sorted(agents.items()):
    rows.append(
        {
            "Agent": agent,
            "Status": event.get("status"),
            "Current Task": event.get("task"),
            "Model": event.get("model"),
            "Last Event": event.get("event_type"),
            "Output": event.get("output_path") or "",
        }
    )
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.subheader("Role Outputs")
tabs = st.tabs([row["Agent"] for row in rows])
for tab, row in zip(tabs, rows):
    with tab:
        st.markdown(status_badge(str(row["Status"])), unsafe_allow_html=True)
        output = rel_path(row["Output"])
        if output and output.is_file() and output.suffix == ".md":
            st.caption(str(output))
            st.markdown(output.read_text(encoding="utf-8"))
        elif output:
            st.code(str(output))
        else:
            st.write("No output yet.")

st.subheader("Event Timeline")
timeline = pd.DataFrame(
    [
        {
            "Time": event.get("timestamp"),
            "Agent": event.get("agent"),
            "Event": event.get("event_type"),
            "Status": event.get("status"),
            "Message": event.get("message"),
        }
        for event in run_events
    ]
)
st.dataframe(timeline, use_container_width=True, hide_index=True)

final_report = ROOT / "demo" / "final-output.md"
st.subheader("Final Report Preview")
if final_report.exists():
    st.markdown(final_report.read_text(encoding="utf-8"))
else:
    st.info("Final report has not been written yet.")

st.caption(f"Loaded {len(events)} total events from {EVENTS_PATH}. Refresh the browser or press R to reload.")

