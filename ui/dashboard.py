"""Streamlit dashboard for the ai_company orchestrator.

Layout
- Sidebar: provider availability, per-task model overrides, accounting snapshot.
- Main:
    * Task input + start button (or pick from queue).
    * Current workflow timeline (analyze → plan → code → test → review → commit).
    * Active checkpoint card: shows the interrupt payload (plan / diff / commit
      message) with Approve / Reject / Edit-message buttons that resume the graph.
- Bottom: queue table, memory search, recent activity.

State flow
- A single LangGraph instance + MemorySaver is held in st.session_state so the
  checkpointer survives Streamlit re-runs within a session.
- We capture events from `graph.stream(...)` into st.session_state.events and
  the latest interrupt payload into st.session_state.pending_interrupt. Each
  button click reruns the script; we then call `graph.stream(Command(resume=...))`
  to continue.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Make package imports work when run via `streamlit run ui/dashboard.py`.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import streamlit as st
from langgraph.types import Command

from orchestrator.graph import build_graph, new_workflow_id
from router import ModelRouter
from storage import accounting as accounting_store
from storage import memory as memory_store
from storage import queue as task_queue


# ---------- session bootstrap ----------


def _init_state() -> None:
    st.session_state.setdefault("graph", build_graph())
    st.session_state.setdefault("workflow_id", None)
    st.session_state.setdefault("events", [])
    st.session_state.setdefault("pending_interrupt", None)  # dict|None
    st.session_state.setdefault("finished", False)
    st.session_state.setdefault("provider_override", None)
    st.session_state.setdefault("selected_model", "")
    st.session_state.setdefault("activity", [])


def _config() -> dict:
    return {"configurable": {"thread_id": st.session_state.workflow_id}}


def _drain(stream) -> None:
    """Consume the graph event stream until an interrupt or END."""
    pending = None
    for ev in stream:
        if "__interrupt__" in ev:
            interrupts = ev["__interrupt__"]
            pending = interrupts[0].value if interrupts else None
            st.session_state.pending_interrupt = pending
            return
        st.session_state.events.append(ev)
        st.session_state.activity.append(_format_event(ev))
    st.session_state.pending_interrupt = None
    st.session_state.finished = True


def _format_event(ev: dict) -> str:
    parts = []
    for node, payload in ev.items():
        if node == "__interrupt__":
            continue
        summary = "ok"
        if isinstance(payload, dict):
            for k in ("analysis", "review_summary", "commit_sha", "commit_message", "error"):
                if k in payload and payload[k]:
                    summary = f"{k}={str(payload[k])[:80]}"
                    break
            if "test_results" in payload and payload["test_results"]:
                summary = f"tests passed={payload['test_results'].get('passed')}"
            if "plan" in payload and payload["plan"]:
                summary = f"plan: {len(payload['plan'])} step(s)"
            if "code_changes" in payload and payload["code_changes"]:
                summary = f"code: {len(payload['code_changes'])} file(s)"
        parts.append(f"{node}: {summary}")
    return " | ".join(parts)


def _start_workflow(task: str) -> None:
    wf = new_workflow_id()
    st.session_state.workflow_id = wf
    st.session_state.events = []
    st.session_state.pending_interrupt = None
    st.session_state.finished = False
    st.session_state.activity.append(f"▶ workflow {wf} started: {task!r}")
    g = st.session_state.graph
    _drain(g.stream({"task": task, "workflow_id": wf}, config=_config()))


def _resume(decision: Any) -> None:
    g = st.session_state.graph
    st.session_state.activity.append(f"⤴ resume: {decision}")
    _drain(g.stream(Command(resume=decision), config=_config()))


# ---------- UI helpers ----------


def _sidebar() -> None:
    st.sidebar.header("Providers")
    router = ModelRouter()
    for name, prov in router.providers.items():
        marker = "✅" if prov.is_available() else "❌"
        st.sidebar.write(f"{marker} {name} — default `{prov.default_model()}`")

    st.sidebar.header("Model override (this session)")
    st.session_state.selected_model = st.sidebar.text_input(
        "Force model id (provider:model or bare)",
        value=st.session_state.get("selected_model", ""),
        placeholder="e.g. claude-haiku-4-5",
    )
    if st.session_state.selected_model:
        os.environ["ROUTER_MODEL_PLAN"] = st.session_state.selected_model
        os.environ["ROUTER_MODEL_ANALYZE"] = st.session_state.selected_model
        os.environ["ROUTER_MODEL_CODE"] = st.session_state.selected_model
        os.environ["ROUTER_MODEL_REVIEW"] = st.session_state.selected_model

    st.sidebar.header("Accounting")
    rep = accounting_store.report(workflow_id=st.session_state.workflow_id)
    st.sidebar.metric("Calls (this workflow)", rep["total_calls"])
    st.sidebar.metric("Cost USD (this workflow)", f"${rep['total_cost_usd']:.4f}")
    rep_all = accounting_store.report()
    st.sidebar.metric("Total cost (all time)", f"${rep_all['total_cost_usd']:.4f}")


def _render_interrupt(payload: dict) -> None:
    kind = payload.get("kind", "?")
    st.subheader(f"Checkpoint: {kind}")
    if kind == "review_plan":
        st.markdown("**Analysis**")
        st.write(payload.get("analysis", ""))
        st.markdown("**Plan**")
        for i, step in enumerate(payload.get("plan", []) or [], 1):
            st.markdown(f"**{i}. {step.get('title', '')}**")
            st.caption(step.get("detail", ""))
            if step.get("files"):
                st.code("\n".join(step["files"]), language="text")
    elif kind == "review_code":
        st.markdown("**Test result**")
        tr = payload.get("test_results") or {}
        st.write(f"passed: `{tr.get('passed')}` returncode: `{tr.get('returncode')}`")
        if tr.get("stdout"):
            with st.expander("stdout (last 4 KB)"):
                st.code(tr["stdout"])
        st.markdown("**Diffs**")
        for ch in payload.get("code_changes", []) or []:
            with st.expander(ch.get("path", "?")):
                st.code(ch.get("diff", ""), language="diff")
    elif kind == "review_commit":
        st.markdown("**Commit message (editable)**")
        new_msg = st.text_area(
            "commit message",
            value=payload.get("commit_message") or "",
            height=120,
            key="commit_msg_edit",
            label_visibility="collapsed",
        )
        st.markdown("**Files**")
        for ch in payload.get("code_changes", []) or []:
            st.write(f"- `{ch.get('path')}`")

    cols = st.columns(3)
    if cols[0].button("✅ Approve", type="primary", use_container_width=True):
        decision: dict = {"approved": True}
        if kind == "review_commit":
            decision["commit_message"] = st.session_state.get("commit_msg_edit") or payload.get("commit_message") or ""
        _resume(decision)
        st.rerun()
    if cols[1].button("✋ Reject", use_container_width=True):
        _resume({"approved": False, "reason": "rejected via dashboard"})
        st.rerun()
    reason = cols[2].text_input("Reject with reason", key="reject_reason", label_visibility="collapsed", placeholder="reject with reason…")
    if reason and cols[2].button("Send reason", use_container_width=True):
        _resume({"approved": False, "reason": reason})
        st.rerun()


def _render_timeline() -> None:
    if not st.session_state.events:
        return
    st.subheader("Workflow timeline")
    for ev in st.session_state.events:
        for node, payload in ev.items():
            if node == "__interrupt__":
                continue
            with st.expander(node):
                if isinstance(payload, dict):
                    for k, v in payload.items():
                        if k == "history":
                            continue
                        if isinstance(v, (dict, list)):
                            st.code(json.dumps(v, indent=2, default=str)[:4000], language="json")
                        else:
                            st.write(f"**{k}**: {v}")


def _render_queue() -> None:
    st.subheader("Queue")
    counts = task_queue.status_counts()
    cols = st.columns(5)
    for col, key in zip(cols, ("pending", "in_progress", "done", "failed", "cancelled")):
        col.metric(key, counts.get(key, 0))
    with st.form("queue_push"):
        c1, c2, c3 = st.columns([3, 1, 1])
        new_task = c1.text_input("Add task", label_visibility="collapsed", placeholder="describe a task to queue…")
        prio = c2.number_input("priority", value=0, step=1, label_visibility="collapsed")
        submit = c3.form_submit_button("Queue")
        if submit and new_task:
            task_queue.push(new_task, priority=int(prio))
            st.rerun()
    tasks = task_queue.list_tasks(limit=20)
    if tasks:
        st.table(
            [
                {"id": t.id, "status": t.status, "prio": t.priority, "task": t.task[:80]}
                for t in tasks
            ]
        )
    # Click-to-run from queue
    pending = task_queue.list_tasks(status="pending", limit=20)
    if pending:
        opts = {f"#{t.id} [{t.priority}] {t.task[:60]}": t for t in pending}
        choice = st.selectbox("Run a queued task", ["—"] + list(opts.keys()))
        if choice != "—" and st.button("Pop & run"):
            t = opts[choice]
            task_queue.pop()  # marks in_progress
            _start_workflow(t.task)
            st.rerun()


def _render_memory() -> None:
    with st.expander("Memory search"):
        q = st.text_input("query", key="memsearch_q")
        if q:
            hits = memory_store.search(q, limit=8)
            for h in hits:
                st.markdown(f"**#{h.id}** · {h.kind} · workflow `{h.workflow_id}`")
                st.caption(h.text[:400])


def _render_activity() -> None:
    with st.expander("Activity (this session)", expanded=False):
        for line in reversed(st.session_state.activity[-50:]):
            st.text(line)


# ---------- page ----------


def main() -> None:
    st.set_page_config(page_title="ai_company", layout="wide")
    st.title("ai_company orchestrator")
    _init_state()
    _sidebar()

    st.markdown("### New task")
    task = st.text_input("Describe the change you want made", key="task_input", placeholder='e.g. "Add a --version flag to cli.py"')
    cols = st.columns([1, 1, 5])
    if cols[0].button("Run", type="primary", disabled=not task or st.session_state.pending_interrupt is not None):
        _start_workflow(task)
        st.rerun()
    if cols[1].button("Reset", disabled=st.session_state.workflow_id is None):
        for k in ("workflow_id", "events", "pending_interrupt", "finished", "activity"):
            st.session_state.pop(k, None)
        st.rerun()

    if st.session_state.workflow_id:
        st.caption(f"workflow: `{st.session_state.workflow_id}` · finished: `{st.session_state.finished}`")

    if st.session_state.pending_interrupt:
        _render_interrupt(st.session_state.pending_interrupt)

    _render_timeline()

    st.markdown("---")
    _render_queue()
    _render_memory()
    _render_activity()


if __name__ == "__main__" or True:
    # Streamlit imports the module, so executing main() at import is required.
    main()
