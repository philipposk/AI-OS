# ai_company — TODO / pending decisions

> **What this is.** A semi-autonomous multi-agent coding orchestrator. LangGraph
> state machine that takes a task, plans it, edits files, runs tests, and asks
> a human to approve at three checkpoints (plan → code → commit). One process
> exposes a Streamlit dashboard, a Slack bot, a Telegram bot, a Voice panel, an
> OpenAI-compatible HTTP shim (for glass / LangChain / curl), and a CLI.
>
> Free by default: rotates across OpenRouter/Groq/NVIDIA NIM free tiers + Ollama
> local; falls back to Anthropic only if you set `ANTHROPIC_API_KEY`. Per-call
> cost recorded in SQLite; budget guards + circuit breaker prevent runaway
> spend. Memory: SQLite FTS5 by default, semantic via Ollama or
> sentence-transformers when available, mirrored to an Obsidian vault when
> `OBSIDIAN_VAULT_PATH` is set. 237/237 tests pass.

Living list of things on hold or worth doing next. Closed items get crossed
out, not deleted, so the history of what was considered stays visible.

## Action items (you)

- [ ] **Publish `llm-free-rotator` 0.1.0 to PyPI**
  - Visit https://pypi.org/manage/account/publishing/
  - Add pending publisher: project `llm-free-rotator`, owner `philipposk`,
    repo `llm-free-rotator`, workflow `publish.yml`, environment `pypi`
  - Then: `cd "/Users/phktistakis/Devoloper Projects/llm-free-rotator"
    && git tag v0.1.0 && git push origin v0.1.0
    && gh release create v0.1.0 --generate-notes`
  - Manual fallback: `.venv/bin/python -m twine upload dist/*`
    (PyPI token at https://pypi.org/manage/account/token/)

- [ ] **Rotate exposed Anthropic key.** The key in `ai_company/.env` was
  shared in chat history. Rotate at https://console.anthropic.com when
  convenient.

- [ ] **OCI deploy dry-run.** Fill `infrastructure/terraform.tfvars` from
  `terraform.tfvars.example`, run `terraform init && terraform plan`, then
  `terraform apply` when ready.

## Decisions on hold

- [ ] **Obsidian second-brain (b) — done.** `storage/obsidian.py` mirrors
  memory.add() into `$OBSIDIAN_VAULT_PATH/ai_company/<workflow>/<id-slug>.md`
  with YAML frontmatter; analyze_node also pulls top-3 substring matches
  from the vault. Set `OBSIDIAN_VAULT_PATH` to enable. Path (a) — pointing
  [logancyang/obsidian-copilot](https://github.com/logancyang/obsidian-copilot)
  at our shim — is still optional; tracked as Phase U in the build plan.

- [ ] **Git/versioning visibility — done.** Dashboard now has a Git panel
  (recent commits, scope toggle to workflow-touched files, one-click
  revert). `tools/git_ops` gained `gh_pr_view` / `gh_pr_list` /
  `gh_issue_view` / `gh_issue_list` / `gh_context_for_task` so
  analyze_node pulls #123 + URL refs out of the user task and injects
  PR/issue body as context. MCP server still skipped.

- [ ] **Jarvis desktop assistant** ([pickle-com/glass]
  (https://github.com/pickle-com/glass) or similar). Point it at our
  FastAPI shim at `http://localhost:8765/v1`. Decide after publishing
  rotator and getting Obsidian wired.

- [ ] **Real video call with the agent.** Web-RTC peer + screen capture +
  TTS overlay. 1–2 weeks of work. Defer; current path (live dashboard
  narration + per-checkpoint screenshot/voice in Slack/Telegram) covers
  90% of the need at 10% of the cost.

## Done (recent)

- [x] Phase A–H — initial rebuild of orchestrator (commits 92e0bff → 00b3456)
- [x] Phase I — generalised free-model rotation to Groq + NVIDIA (0769a39)
- [x] Phase J — semantic memory via Ollama / sentence-transformers (13b2239)
- [x] Phase K — token streaming across all providers + dashboard (4fa6590)
- [x] Phase L — Slack bot with Block Kit checkpoint buttons (50cc71a)
- [x] Phase M — Telegram bot with inline keyboards (50f6283)
- [x] Phase N — voice (Groq Whisper STT + browser TTS) (8e52184)
- [x] Phase O+P — OpenAI-compat API shim + dashboard narration (4568be2)
- [x] Side: `llm-free-rotator` extracted to standalone GitHub repo with
  MIT licence, pyproject, GitHub Actions Trusted-Publishing workflow,
  CHANGELOG (fde3cfb on https://github.com/philipposk/llm-free-rotator)

## In progress

(none)

## Done (additional)

- [x] Phase Q — Slack + Telegram screenshot + voice-note per checkpoint (470e58f)
- [x] Phase R — Obsidian vault mirror + gh PR/issue wrappers (21b2fe4)
- [x] Phase S — Dashboard git panel (recent commits + revert + PR/issue) (965cda8)
- [x] Phase T — cli.py check --live smoke (0ff0bdd / f0bb5d8)
- [x] Phase U — Obsidian-copilot setup docs (f0bb5d8)
- [x] Phase V — Cost budgets + per-provider circuit breaker (07b9057)
- [x] Phase W — Web search cascade (Brave/Serper/Tavily/DDG) (c7039aa)
- [x] Phase X — Multi-agent crew (Planner/Critic + Reviewer/Tester) (b34f437)
- [x] Phase Y — Multi-cloud Terraform: Oracle + AWS + GCP + Azure + Hetzner + DigitalOcean (b0ef172)
- [x] Glass wiring + minimal Claude-style SPA at /ui + /v1/workflows API (42f76a9)
- [x] PR review CLI extension — severity tags, crew mode, GH Action, inline fix suggestions (f138882)
- [x] Live workflow run end-to-end — agent committed 51b710c via Groq for $0
- [x] Vision support — image content blocks routed to Anthropic / OpenRouter (e5203ac)
- [x] Plan/analyze/review prompt tuning — 60% fewer tokens, no filler steps (71271aa)
- [x] Production hardening — JSON logs + /v1/metrics Prometheus + optional Sentry (745c798)

## What it can do today

Run a task end-to-end:
```
.venv/bin/python cli.py run "Add a --version flag to cli.py"
```

Or via the dashboard:
```
.venv/bin/streamlit run ui/dashboard.py
```

Or the OpenAI-compatible HTTP shim (for glass / LangChain / curl / Obsidian):
```
.venv/bin/python -m api.server                  # 127.0.0.1:8765
curl -s http://localhost:8765/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"simple","messages":[{"role":"user","content":"hi"}]}'
```

Or chat from Slack / Telegram:
```
SLACK_BOT_TOKEN=... SLACK_APP_TOKEN=... .venv/bin/python -m communication.slack
TELEGRAM_BOT_TOKEN=...                  .venv/bin/python -m communication.telegram
```

Interfaces summary:

| Surface | Path | What it does |
|---|---|---|
| Dashboard | `streamlit run ui/dashboard.py` → http://localhost:8501 | Plan/code/commit checkpoints, live token narration, voice mic, git panel with revert, queue, memory search |
| Minimal SPA | http://localhost:8765/ui | Claude-style chat, talks straight to /v1/chat/completions |
| CLI | `cli.py run/providers/queue/memory/obsidian/voice/web/free-models/accounting/check` | All of the above without Streamlit |
| HTTP shim | http://localhost:8765/v1 | OpenAI-compatible chat + models + metrics endpoints |
| Slack bot | `/ai-run <task>` | Per-checkpoint Block Kit buttons + PNG + voice MP3 |
| Telegram bot | `/ai_run <task>` | Inline keyboard + PNG + voice MP3 |
| GH Action | `.github/workflows/pr-review.yml` | Auto PR review with severity tags + crew mode |
