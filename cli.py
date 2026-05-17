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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
