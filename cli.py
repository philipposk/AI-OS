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
import re
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
    if kind == "budget_exceeded":
        # Phase U: terminal flow for the circuit breaker — either raise the
        # ceiling (number) or abort (anything else / empty).
        while True:
            ans = input("Raise ceiling to USD (blank = abort): ").strip()
            if not ans:
                return {"approved": False, "reason": "budget abort"}
            try:
                return {"approved": True, "raise_to": float(ans)}
            except ValueError:
                print("Not a number. Try again or press Enter to abort.")
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

    payload: Any = {"task": args.task, "workflow_id": wf,
                    "search_enabled": bool(getattr(args, "search", False)),
                    "crew_mode": (True if getattr(args, "crew", False) else None)}
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
    if getattr(args, "live", False):
        _live_smoke_test(r)
    return 0


def _live_smoke_test(router: ModelRouter) -> None:
    """Phase T: 1-token "hi" round-trip per available provider.

    Diagnostic-only. Cost ~$0 because completions are capped at 1 token.
    Failures print FAIL but do not flip the exit code — this is a smoke test,
    not a gate.
    """
    import time

    print("\nLive provider smoke test (1-token hello each):")
    for name, prov in router.providers.items():
        if not prov.is_available():
            print(f"  {name:11s} SKIP (no credentials)")
            continue
        model = prov.default_model()
        t0 = time.perf_counter()
        try:
            res = prov.chat(
                [{"role": "user", "content": "hi"}],
                model=model,
                max_tokens=1,
                temperature=0.0,
            )
            dt = (time.perf_counter() - t0) * 1000
            print(f"  {name:11s} OK   {dt:6.0f}ms  model={model}  in={res.prompt_tokens} out={res.completion_tokens}")
        except Exception as e:  # noqa: BLE001
            dt = (time.perf_counter() - t0) * 1000
            msg = str(e).splitlines()[0][:160]
            print(f"  {name:11s} FAIL {dt:6.0f}ms  model={model}  err={msg}")


def cmd_review_pr(args: argparse.Namespace) -> int:
    """Phase Z: CodeRabbit-style automated PR review."""
    from tools.pr_review import review_pr

    # Accept either a number or a github URL.
    target = args.pr
    m = re.search(r"/pull/(\d+)", target)
    if m:
        target = m.group(1)

    print(f"# reviewing PR #{target} (dry_run={args.dry_run})")
    try:
        result = review_pr(target, dry_run=args.dry_run)
    except RuntimeError as e:
        print(f"error: {e}")
        return 2

    print("\n=== Summary ===")
    print(result.summary)
    print(f"\n=== Line comments ({len(result.line_comments)}) ===")
    for lc in result.line_comments:
        print(f"  {lc.path}:{lc.line}  {lc.body[:140]}")
    if not args.dry_run:
        print(f"\nposted summary: {result.posted}")
        print(f"line comments posted: {result.posted_line_comments} "
              f"(skipped: {result.skipped_line_comments})")
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


def cmd_obsidian(args: argparse.Namespace) -> int:
    from storage import obsidian as obs
    if args.obsidian_cmd == "status":
        root = obs.vault_root()
        if root is None:
            print("vault: disabled (set OBSIDIAN_VAULT_PATH to enable)")
            return 0
        print(f"vault: {root}")
        sub = root / "ai_company"
        count = sum(1 for _ in sub.rglob("*.md")) if sub.exists() else 0
        print(f"mirrored notes: {count}")
        return 0
    if args.obsidian_cmd == "export":
        n = obs.bulk_export()
        print(f"exported {n} note(s) to vault")
        return 0
    if args.obsidian_cmd == "search":
        hits = obs.search_vault(args.query, limit=args.limit)
        if not hits:
            print("(no matches)")
        for h in hits:
            print(f"score={h['score']}  {h['path']}")
            print(f"  {h['snippet']}")
        return 0
    return 2


def cmd_voice(args: argparse.Namespace) -> int:
    from router.transcription import get_transcriber

    if args.voice_cmd == "backend":
        t = get_transcriber()
        if t is None:
            print("STT backend: none. Set GROQ_API_KEY to enable.")
        else:
            print(f"STT backend: {t.name} (model {t.model})")
        return 0
    if args.voice_cmd == "transcribe":
        t = get_transcriber()
        if t is None:
            print("STT backend: none. Set GROQ_API_KEY first.")
            return 2
        from pathlib import Path
        path = Path(args.path)
        mime = {"webm": "audio/webm", "mp3": "audio/mpeg", "wav": "audio/wav",
                "m4a": "audio/mp4", "ogg": "audio/ogg", "flac": "audio/flac"}.get(path.suffix.lstrip(".").lower(), "audio/webm")
        tr = t.transcribe(path.read_bytes(), filename=path.name, mime_type=mime, language=args.language)
        print(tr.text)
        return 0
    return 2


def cmd_free_models(args: argparse.Namespace) -> int:
    prov = args.provider
    from dotenv import dotenv_values
    env = (dotenv_values(".env") or {})
    key_var = {"openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY", "nvidia": "NVCF_API_KEY"}[prov]
    key = env.get(key_var) or os.environ.get(key_var)
    if not key:
        print(f"{key_var} not set")
        return 2
    if prov == "openrouter":
        from router.openrouter_free import OpenRouterRotator as R
    elif prov == "groq":
        from router.groq_free import GroqRotator as R
    else:
        from router.nvidia_free import NvidiaRotator as R
    r = R()
    models, last_used = r.get_models(key, force_refresh=args.free_cmd == "refresh")
    print(f"# {prov}: {len(models)} free models  (last used: {last_used or '—'})")
    for m in models:
        print(f"  {m.weight:5.1f}  ctx={m.context_length:>7d}  {m.id}")
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    if args.memory_cmd == "search":
        hits = memory_store.search(args.query, kind=args.kind, limit=args.limit)
        if not hits:
            print("(no matches)")
        for h in hits:
            score = f"{h.score:.3f}" if h.score is not None else "—"
            print(f"#{h.id} kind={h.kind} wf={h.workflow_id} score={score}")
            print(f"  {h.text[:300]}")
        return 0
    if args.memory_cmd == "count":
        print(memory_store.count())
        return 0
    if args.memory_cmd == "reembed":
        n = memory_store.reembed_all()
        print(f"re-embedded {n} rows")
        return 0
    if args.memory_cmd == "backend":
        from storage.embeddings import get_embedder
        e = get_embedder()
        if e is None:
            print("backend: none (FTS5 keyword only). Run Ollama or `pip install sentence-transformers`.")
        else:
            print(f"backend: {e.name} dim={e.dim}")
        return 0
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai_company", description="AI orchestrator CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a workflow")
    run.add_argument("task", help="Free-form task description")
    run.add_argument("--workflow-id", help="Resume an existing workflow id")
    run.add_argument("--search", action="store_true",
                     help="Inject web-search results into the plan-prompt (Brave / Serper / Tavily / DDG cascade).")
    run.add_argument("--crew", action="store_true",
                     help="Use multi-agent crew (Planner + Critic) for the plan step instead of a single LLM.")
    run.set_defaults(func=cmd_run)

    sub.add_parser("providers", help="List provider availability").set_defaults(func=cmd_providers)
    acc = sub.add_parser("accounting", help="Print accounting summary")
    acc.add_argument("--json", action="store_true")
    acc.set_defaults(func=cmd_accounting)
    chk = sub.add_parser("check", help="Sanity-check the install")
    chk.add_argument("--live", action="store_true",
                     help="Also send a 1-token hello to each available provider (diagnostic).")
    chk.set_defaults(func=cmd_check)

    pr = sub.add_parser("review-pr", help="CodeRabbit-style automated PR review (Reviewer + Critic crew → gh)")
    pr.add_argument("pr", help="PR number or full github.com PR URL")
    pr.add_argument("--dry-run", action="store_true",
                    help="Print the review payload instead of posting to GitHub")
    pr.set_defaults(func=cmd_review_pr)

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
    msub.add_parser("reembed", help="Backfill embeddings for old rows (idempotent)")
    msub.add_parser("backend", help="Show active embeddings backend")
    m.set_defaults(func=cmd_memory)

    fr = sub.add_parser("free-models", help="Free model list / rotation for any provider")
    frsub = fr.add_subparsers(dest="free_cmd", required=True)
    for sc in ("list", "refresh"):
        p_ = frsub.add_parser(sc, help=f"{sc} cached free models")
        p_.add_argument("--provider", choices=("openrouter", "groq", "nvidia"), default="openrouter")
    fr.set_defaults(func=cmd_free_models)

    v = sub.add_parser("voice", help="Speech-to-text utilities")
    vsub = v.add_subparsers(dest="voice_cmd", required=True)
    vsub.add_parser("backend", help="Show active STT backend")
    vtrans = vsub.add_parser("transcribe", help="Transcribe an audio file")
    vtrans.add_argument("path", help="Path to audio file (webm/mp3/wav/m4a/ogg)")
    vtrans.add_argument("--language", default=None, help="ISO language code (auto-detect if omitted)")
    v.set_defaults(func=cmd_voice)

    o = sub.add_parser("obsidian", help="Obsidian vault mirror utilities")
    osub = o.add_subparsers(dest="obsidian_cmd", required=True)
    osub.add_parser("status", help="Show vault path + counts")
    osub.add_parser("export", help="Mirror every memory_docs row into the vault")
    osearch = osub.add_parser("search", help="Substring search across the vault")
    osearch.add_argument("query")
    osearch.add_argument("--limit", type=int, default=10)
    o.set_defaults(func=cmd_obsidian)

    w = sub.add_parser("web", help="Web search utilities")
    wsub = w.add_subparsers(dest="web_cmd", required=True)
    wsub.add_parser("backend", help="Show which web search backend is active")
    wsearch = wsub.add_parser("search", help="Search the web")
    wsearch.add_argument("query")
    wsearch.add_argument("-k", "--top", type=int, default=5)
    w.set_defaults(func=cmd_web)

    mp = sub.add_parser("model-perf", help="Per-(provider, model, task_type) success rates from retrospectives")
    mp.add_argument("--task-type", default=None,
                    help="Filter to one task_type (analyze/plan/code/review/...)")
    mp.add_argument("--best", action="store_true",
                    help="Show only the best model per task_type")
    mp.add_argument("--min-workflows", type=int, default=5)
    mp.set_defaults(func=cmd_model_perf)

    tn = sub.add_parser("tune", help="DSPy prompt tuning from retrospectives")
    tnsub = tn.add_subparsers(dest="tune_cmd", required=True)
    tnsub.add_parser("status", help="Show retrospective counts + learned prompts on disk")
    tnrun = tnsub.add_parser("run", help="Run MIPROv2 on accumulated retrospectives")
    tnrun.add_argument("--task-type", choices=("analyze", "plan", "review"), default="plan")
    tnrun.add_argument("--min-examples", type=int, default=5)
    tnauto = tnsub.add_parser("auto", help="Cron-friendly: tune any task_type whose new-retrospective delta crossed threshold")
    tnauto.add_argument("--min-total", type=int, default=20)
    tnauto.add_argument("--min-new", type=int, default=10)
    tn.set_defaults(func=cmd_tune)

    sk = sub.add_parser("slack", help="Manage Slack pending-ticket queue")
    sksub = sk.add_subparsers(dest="slack_cmd", required=True)
    skl = sksub.add_parser("list", help="List pending Slack tickets")
    skl.add_argument("--limit", type=int, default=50)
    ska = sksub.add_parser("approve", help="Approve a pending ticket → start workflow")
    ska.add_argument("pending_id", help="Pending ticket ID (from 'slack list')")
    sks = sksub.add_parser("skip", help="Skip / dismiss a pending ticket")
    sks.add_argument("pending_id", help="Pending ticket ID (from 'slack list')")
    sk.set_defaults(func=cmd_slack)

    return p


def cmd_model_perf(args: argparse.Namespace) -> int:
    """Print per-model success rates from joined ledger+retrospectives."""
    from storage import model_performance as mp

    if args.best:
        out = {}
        for tt in ("analyze", "plan", "code", "review"):
            choice = mp.best_model_for(tt, min_workflows=args.min_workflows)
            out[tt] = {"provider": choice[0], "model": choice[1]} if choice else None
        print(json.dumps(out, indent=2))
        return 0

    rows = mp.report(task_type=args.task_type)
    print(json.dumps([
        {
            "provider": r.provider, "model": r.model, "task_type": r.task_type,
            "n_workflows": r.n_workflows, "n_successes": r.n_successes,
            "success_rate": round(r.success_rate, 4),
            "avg_retries": round(r.avg_retries, 2),
            "avg_cost_per_call": round(r.avg_cost_per_call, 6),
        }
        for r in rows
    ], indent=2))
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    """DSPy prompt tuning entry point.

    `status` — show retrospective counts + which prompts have been learned.
    `run`    — pull training set from retrospectives, run MIPROv2 on the
               chosen task_type, save to tuning/learned_prompts.json.
    """
    from storage import retrospectives as r
    from tuning import (
        LEARNED_PROMPTS_PATH, PromptTuner, load_learned_prompts,
        training_examples_from_retrospectives,
    )

    if args.tune_cmd == "status":
        agg = r.aggregate()
        learned = load_learned_prompts()
        print(json.dumps({
            "retrospectives": agg,
            "learned_prompts_path": str(LEARNED_PROMPTS_PATH),
            "learned_task_types": sorted(learned.keys()),
        }, indent=2))
        return 0

    if args.tune_cmd == "run":
        examples = training_examples_from_retrospectives(min_per_class=args.min_examples)
        if not examples:
            print(f"need at least {args.min_examples} positive and {args.min_examples} negative "
                  f"retrospectives — run more workflows first.")
            return 1
        tuner = PromptTuner()
        result = tuner.tune(args.task_type, examples=examples)
        path = tuner.save([result])
        print(json.dumps({
            "task_type": result.task_type,
            "n_examples": result.n_examples,
            "changed": result.tuned_prompt != result.base_prompt,
            "saved_to": str(path),
        }, indent=2))
        return 0

    if args.tune_cmd == "auto":
        from tuning import auto_tune
        report = auto_tune(min_total=args.min_total, min_new=args.min_new)
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    return 1


def cmd_web(args):
    from tools import web_search as ws
    if args.web_cmd == "backend":
        print(f"web search backend: {ws.available_backend()}")
        return 0
    if args.web_cmd == "search":
        hits = ws.search(args.query, k=args.top)
        if not hits:
            print("(no results)")
        for h in hits:
            print(f"- {h['title']}  [{h['source']}]")
            print(f"  {h['url']}")
            if h.get("snippet"):
                print(f"  {h['snippet'][:200]}")
        return 0
    return 2


def cmd_slack(args: argparse.Namespace) -> int:
    """Manage Slack pending-ticket queue from the terminal."""
    from storage import slack_tickets as st

    if args.slack_cmd == "list":
        items = st.list_pending(limit=args.limit)
        if not items:
            print("(no pending tickets)")
            return 0
        for p in items:
            print(f"  {p.pending_id}  U:{p.user}  #{p.channel}  {p.created_at[:19]}")
            print(f"    task: {p.task[:120]}")
        return 0

    if args.slack_cmd in ("approve", "skip"):
        pending_id = args.pending_id
        ticket = st.pop_pending(pending_id)
        if ticket is None:
            print(f"No pending ticket '{pending_id}' — already handled or expired.")
            return 1
        if args.slack_cmd == "skip":
            print(f"Skipped '{pending_id}'.")
            return 0
        # approve → start workflow
        from communication.dispatcher import WorkflowDispatcher
        def _print_post(channel, thread_ts, text, blocks):
            print(f"[→slack {channel}] {text[:200]}")
        d = WorkflowDispatcher(_print_post)
        wid = d.start_workflow(
            task=ticket.task,
            channel=ticket.channel,
            thread_ts=ticket.thread_ts,
            user=ticket.user,
        )
        print(f"Workflow {wid} started for ticket '{pending_id}'.")
        return 0

    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
