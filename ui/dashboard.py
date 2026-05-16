# Web UI Dashboard - Browser-based interface
import streamlit as st
import json
from orchestration import Orchestrator

st.set_page_config(page_title="GStack Dashboard", layout="wide")
st.title("GStack AI Company Dashboard")
# Dark‑mode toggle
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False
dark = st.sidebar.checkbox("Dark mode", value=False)
st.session_state.dark_mode = dark
if dark:
    st.markdown("""<style>body {background-color:#111;color:#eee;}</style>""", unsafe_allow_html=True)
else:
    st.markdown("""<style>body {background-color:#fff;color:#111;}</style>""", unsafe_allow_html=True)

# Initialize orchestrator (single instance per session)
if "orch" not in st.session_state:
    st.session_state.orch = Orchestrator()
orch = st.session_state.orch

# Sidebar: worker status
if orch.nvidia is None:
    st.sidebar.warning("NVIDIA client not available – check NVCF_API_KEY environment variable.")
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
        # Log activity for UI
        orch.log_activity(f"Task '{task}' executed with model {selected_model}")
        # Refresh in‑session log display
        st.session_state.activity_log.append(f"Task '{task}' run with model {selected_model}")

# Activity log (persistent)
st.header("Activity Log")
# Load persisted log from orchestrator if available
if "activity_log" not in st.session_state:
    # Try to read from orchestrator's log file (if orchestrator exists)
    try:
        with open(orch.activity_log_path, "r", encoding="utf-8") as f:
            lines = [line.strip().split(' - ', 1)[1] for line in f.readlines() if ' - ' in line]
            st.session_state.activity_log = lines
    except Exception:
        st.session_state.activity_log = []
# Show log entries
for entry in st.session_state.activity_log:
    st.write(f"- {entry}")

# Add a manual entry (optional)
if st.button("Add Manual Log Entry"):
    manual = st.text_input("Log message:")
    if manual:
        orch.log_activity(manual)
        st.session_state.activity_log.append(manual)