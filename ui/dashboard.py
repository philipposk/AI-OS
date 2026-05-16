# Web UI Dashboard - Browser-based interface
import streamlit as st
import json
from orchestration import Orchestrator

st.title("GStack AI Company Dashboard")

# Initialize orchestrator (single instance per session)
if "orch" not in st.session_state:
    st.session_state.orch = Orchestrator()
orch = st.session_state.orch

# Sidebar: worker status
st.sidebar.title("AI Workers")
worker_status = {
    "orchestrator": "running",
    "aider": "idle",
    "openhands": "idle",
    "context": "active"
}
for worker, status in worker_status.items():
    st.sidebar.write(f"{worker}: {status}")

# Model selection
st.sidebar.subheader("Model Selection")
model_options = [
    "openrouter/free",
    "nvidia-nim/deepseek-v4-flash",
    "openai/gpt-4o-mini",
    "groq/llama-3.1-70b"
]
selected_model = st.sidebar.selectbox("Choose model", model_options, index=0)
# Apply selection
orch.set_model(selected_model)

# Main: Task input
st.header("Current Task")
task = st.text_input("Task description:")
if st.button("Run Task") and task:
    with st.spinner("Executing workflow…"):
        result = orch.execute_workflow(task)
        st.success("Task completed!")
        st.write(result)
        # Show token usage
        usage = orch.accounting.report()
        st.write("**Token usage**")
        st.json(usage)

# Activity log (basic)
st.header("Activity Log")
log = []
if "activity_log" not in st.session_state:
    st.session_state.activity_log = []
for entry in st.session_state.activity_log:
    st.write(f"- {entry}")

# Append new entry after run
if st.button("Add Log Entry"):
    st.session_state.activity_log.append(f"Task '{task}' run with model {selected_model}")