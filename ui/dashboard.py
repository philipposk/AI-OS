# Web UI Dashboard - Browser-based interface
import streamlit as st
import json

st.title("GStack AI Company Dashboard")

st.sidebar.title("AI Workers")
worker_status = {
    "orchestrator": "running",
    "aider": "idle",
    "openhands": "idle",
    "context": "active"
}
for worker, status in worker_status.items():
    st.sidebar.write(f"{worker}: {status}")

st.write("## Current Task")
if st.button("Start New Task"):
    st.session_state.task_input = st.text_input("Task description:")

st.write("## Activity Log")
log = [
    "Task started: Add dark mode",
    "Context loaded",
    "Model selected: groq/llama-3.1-70b",
    "Files edited: 3"
]
for entry in log:
    st.write(f"- {entry}")