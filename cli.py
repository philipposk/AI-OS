"""ai_company CLI — replaces admin.py + init_system.py + task_runner.py.

Subcommands:
    run "<task>"            Start a workflow and walk through interrupts in the terminal.
    providers               List available LLM providers.
    accounting [--json]     Print the token+cost ledger summary.
    check                   Sanity-check the install (imports, env vars).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from orchestrator import build_graph, new_workflow_id
from langgraph.types import Command

from router import ModelRouter
from storage import memory as memory_store
from storage import queue as task_queue
from storage.accounting import report


def _print_event(ev: dict) -> None:
    for node_name, payload in ev.items():
        if node_name == "__interrupt__":
            continue
        print(f"[{node_name}]")
        if isinstance(payload, dict):
            for k, v in payload.items():
                if k == "history":
                    continue
                shown = v if isinstance(v, (str, int, float, bool, type(None))) else json.dumps(v, indent=2, default=str)[:1200]
                print(f"  {k}: {shown}")


def _prompt_decision(payload: dict) -> Any:
    kind = payload.get("kind", "?")
    print(f"\n----- HUMAN CHECKPOINT: {kind} -----")
    print(json.dumps({k: v for k, v in payload.items() if k != "kind"}, indent=2, default=str)[:4000])
    while True:
        ans = input("approve? [y/N/reason]: ").strip()
        if ans.lower() in ("y", "yes"):
            return {"approved": True}
        if ans.lower() in ("", "n", "no"):
            return {"approved": False, "reason": "rejected by user"}
        return {"approved": False, "reason": ans}


def cmd_run(args: argparse.Namespace) -> int:
    graph = build_graph()
    wf = args.workflow_id or new_workflow_id()
    config = {"configurable": {"thread_id": wf}}
    print(f"# workflow_id={wf}")

    payload: Any = {"task": args.task, "workflow_id": wf}
    while True:
        interrupted = False
        for ev in graph.stream(payload, config=config):
            if "__interrupt__" in ev:
                interrupted = True
                # ev["__interrupt__"] is a list of Interrupt objects
                interrupt = ev["__interrupt__"][0]
                decision = _prompt_decision(interrupt.value)
                payload = Command(resume=decision)
                break
            _print_event(ev)
        if not interrupted:
            break
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    r = ModelRouter()
    avail = r.available_providers()
    print("Available providers:", avail or "(none — set at least one API key in .env)")
    for name, prov in r.providers.items():
        status = "ok" if prov.is_available() else "missing-key"
        print(f"  {name:11s} {status:11s} default={prov.default_model()}")
    return 0


def cmd_accounting(args: argparse.Namespace) -> int:
    rep = report()
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"Total calls:        {rep['total_calls']}")
        print(f"Prompt tokens:      {rep['total_prompt_tokens']}")
        print(f"Completion tokens:  {rep['total_completion_tokens']}")
        print(f"Total cost (USD):   ${rep['total_cost_usd']:.4f}")
        if rep["by_provider"]:
            print("By provider:")
            for p, v in rep["by_provider"].items():
                print(f"  {p:11s} calls={v['calls']:<4d} cost=${v['cost_usd']:.4f}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    problems = []
    try:
        import langgraph  # noqa: F401
    except ImportError as e:
        problems.append(f"langgraph not importable: {e}")
    try:
        import streamlit  # noqa: F401
    except ImportError as e:
        problems.append(f"streamlit not importable: {e}")
    r = ModelRouter()
    if not r.available_providers():
        problems.append("No LLM provider API key found. Set ANTHROPIC_API_KEY / OPENROUTER_API_KEY / NVCF_API_KEY / GROQ_API_KEY, or run Ollama locally.")
    if problems:
        print("Problems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("All checks passed.")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    if args.queue_cmd == "push":
        t = task_queue.push(args.task, priority=args.priority)
        print(f"queued #{t.id} priority={t.priority}: {t.task!r}")
        return 0
    if args.queue_cmd == "list":
        for t in task_queue.list_tasks(status=args.status, limit=args.limit):
            print(f"#{t.id:<4d} [{t.status:11s}] prio={t.priority} {t.task[:80]}")
        return 0
    if args.queue_cmd == "status":
        counts = task_queue.status_counts()
        for k, v in counts.items():
            print(f"  {k:11s} {v}")
        return 0
    if args.queue_cmd == "cancel":
        task_queue.cancel(args.id)
        print(f"cancelled #{args.id}")
        return 0
    return 2


def cmd_memory(args: argparse.Namespace) -> int:
    if args.memory_cmd == "search":
        hits = memory_store.search(args.query, kind=args.kind, limit=args.limit)
        if not hits:
            print("(no matches)")
        for h in hits:
            score = f"{h.score:.2f}" if h.score is not None else "—"
            print(f"#{h.id} kind={h.kind} wf={h.workflow_id} score={score}")
            print(f"  {h.text[:300]}")
        return 0
    if args.memory_cmd == "count":
        print(memory_store.count())
        return 0
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai_company", description="AI orchestrator CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a workflow")
    run.add_argument("task", help="Free-form task description")
    run.add_argument("--workflow-id", help="Resume an existing workflow id")
    run.set_defaults(func=cmd_run)

    sub.add_parser("providers", help="List provider availability").set_defaults(func=cmd_providers)
    acc = sub.add_parser("accounting", help="Print accounting summary")
    acc.add_argument("--json", action="store_true")
    acc.set_defaults(func=cmd_accounting)
    sub.add_parser("check", help="Sanity-check the install").set_defaults(func=cmd_check)

    q = sub.add_parser("queue", help="Task queue ops")
    qsub = q.add_subparsers(dest="queue_cmd", required=True)
    qpush = qsub.add_parser("push", help="Queue a task")
    qpush.add_argument("task")
    qpush.add_argument("--priority", type=int, default=0)
    qlist = qsub.add_parser("list", help="List tasks")
    qlist.add_argument("--status", help="pending|in_progress|done|failed|cancelled")
    qlist.add_argument("--limit", type=int, default=20)
    qsub.add_parser("status", help="Show counts by status")
    qcancel = qsub.add_parser("cancel", help="Cancel a task")
    qcancel.add_argument("id", type=int)
    q.set_defaults(func=cmd_queue)

    m = sub.add_parser("memory", help="Memory store ops")
    msub = m.add_subparsers(dest="memory_cmd", required=True)
    msearch = msub.add_parser("search", help="FTS5 search over stored notes")
    msearch.add_argument("query")
    msearch.add_argument("--kind", help="analysis|review|note|...")
    msearch.add_argument("--limit", type=int, default=5)
    msub.add_parser("count", help="Count documents")
    m.set_defaults(func=cmd_memory)

    fr = sub.add_parser("free-models", help="OpenRouter free model list / rotation")
    frsub = fr.add_subparsers(dest="free_cmd", required=True)
    frsub.add_parser("list", help="Show cached free model list")
    frsub.add_parser("refresh", help="Force-refresh from OpenRouter /models")
    fr.set_defaults(func=lambda a: __import__("router.openrouter_free", fromlist=["main"]).main([a.free_cmd]))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
