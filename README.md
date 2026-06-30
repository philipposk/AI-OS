# ai_company

Semi-autonomous multi-agent coding orchestrator. You describe a task; a
LangGraph state machine plans it, edits files, runs tests, and stops at
three human-in-the-loop checkpoints (plan → code → commit) before
anything is committed.

Free by default: rotates across OpenRouter / Groq / NVIDIA NIM free tiers
plus Ollama local; only falls back to Anthropic if you set
`ANTHROPIC_API_KEY`. Per-call cost is recorded in SQLite; budget guards
and a per-provider circuit breaker prevent runaway spend. **331 tests
collected; optional extras (streamlit, Pillow, json_repair, edge-tts) skip
when not installed. Zero live LLM calls in CI.**

> **New here / non-technical reader?** Start with
> [docs/explainer.md](docs/explainer.md) — plain-English tour of what
> this is, who it's for, and the ten most common use-cases.

```
User ──► CLI │ Streamlit dashboard │ Minimal SPA │ Slack bot │ Telegram bot │ OpenAI HTTP shim
                                          │
                                          ▼
                  LangGraph state machine (orchestrator/graph.py)
                  do_analyze → do_plan → checkpoint_plan
                                              ↓ approved
                  do_code → do_test → (passed?) → do_review (crew Reviewer/Tester)
                       ↑       ↓ no, retries<3                  ↓
                       └── do_code_retry                checkpoint_commit
                                  ↓ retries≥3                   ↓ approved
                              checkpoint_code              do_commit → END
                                          │
                                          ▼
                  ModelRouter (router/router.py) — task_type → provider+model
                  anthropic │ openrouter │ groq │ nvidia │ ollama  (rotation + fallback + circuit breaker)
                                          │
                                          ▼
                  SQLite state (storage/)
                  queue + memory (FTS5 / semantic) + accounting + retrospectives
                  + model_performance + slack_tickets + Obsidian vault mirror
```

## What it can do today

| Surface | How to start | What it does |
|---|---|---|
| Dashboard | `streamlit run ui/dashboard.py` → http://localhost:8501 | Plan/code/commit checkpoints, live token streaming + narration, voice mic, git panel with revert, queue + memory search, accounting |
| Minimal SPA | run the HTTP shim, open http://localhost:8765/ | Claude-style chat, talks straight to `/v1/chat/completions` (this is what `frontend/` ships) |
| CLI | `python cli.py <subcommand>` | Everything below, terminal-only |
| HTTP shim | `python -m api.server` → http://localhost:8765 | OpenAI-compatible chat + models + accounting + metrics + workflows endpoints |
| Slack bot | `python -m communication.slack` | `/ai-run <task>` + passive ticket detection + per-checkpoint Block Kit buttons with screenshot PNG + voice MP3 |
| Telegram bot | `python -m communication.telegram` | `/ai_run <task>` + ticket pending queue + inline keyboard checkpoints with PNG + voice MP3 |
| Email ingest | `python -m communication.email_ingest` | IMAP poll → ticket detector → pending queue |
| GH Action | `.github/workflows/pr-review.yml` | Auto PR review with severity tags + crew mode + inline fix suggestions |

## Quick start (local Mac)

```bash
cd AI-OS
./infrastructure/deploy.sh    # creates .venv + installs deps + .env from .env.example
source .venv/bin/activate
$EDITOR .env                  # add at least ONE provider key (see Providers below)
python cli.py check           # sanity-check
python cli.py check --live    # also pings each configured provider
streamlit run ui/dashboard.py # http://localhost:8501
```

Or drive from the terminal:

```bash
python cli.py run "Add a --version flag to cli.py"
python cli.py run "..." --search       # let do_plan pre-pull web context
python cli.py run "..." --crew         # multi-agent Reviewer/Tester at review_node
```

## Providers

Any one of:

| Provider     | Env var               | Free tier?            | Default model                              |
|--------------|-----------------------|-----------------------|--------------------------------------------|
| Anthropic    | `ANTHROPIC_API_KEY`   | no                    | `claude-haiku-4-5`                         |
| OpenRouter   | `OPENROUTER_API_KEY`  | yes (`:free` tags)    | `meta-llama/llama-3.2-3b-instruct:free`    |
| NVIDIA NIM   | `NVCF_API_KEY`        | yes (build endpoints) | `meta/llama-3.1-8b-instruct`               |
| Groq         | `GROQ_API_KEY`        | yes (dev tier)        | `llama-3.3-70b-versatile`                  |
| Ollama       | `OLLAMA_BASE_URL`     | local / free          | `llama3.2:3b`                              |

Per-task overrides: `ROUTER_MODEL_ANALYZE`, `_PLAN`, `_CODE`, `_REVIEW`,
`_SUMMARIZE`, `_SIMPLE`. Force a model from the dashboard sidebar.

The router falls back across providers automatically. Free-model
rotation (`router/{groq,openrouter,nvidia}_free.py`) refreshes the live
free-model list per provider; rotation skips models that just rate-limited.

**Vision:** image content blocks are auto-routed to vision-capable
providers (Anthropic, OpenRouter). See [router/vision.py](router/vision.py).

**Cost budgets + circuit breaker** ([orchestrator/budget.py](orchestrator/budget.py),
[router/circuit.py](router/circuit.py)): set `BUDGET_USD_PER_RUN` /
`BUDGET_USD_PER_DAY`; when exceeded, the workflow raises a
`budget_exceeded` interrupt so a human can raise the ceiling or abort.
The circuit breaker takes a provider out of rotation after N
consecutive failures.

## CLI

```bash
python cli.py run "<task>" [--search] [--crew] [--workflow-id ID]
python cli.py providers
python cli.py check [--live]
python cli.py accounting [--json]
python cli.py review-pr <num|url> [--dry-run] [--crew] [--inline] [--severity-threshold low|medium|high]
python cli.py queue push "<task>" [--priority N]
python cli.py queue list [--status …]
python cli.py queue status
python cli.py queue cancel <id>
python cli.py memory search "<query>"
python cli.py memory count
python cli.py memory reembed                  # backfill embeddings for old rows
python cli.py memory backend                  # show active embeddings backend
python cli.py obsidian status | export | search "<query>"
python cli.py voice backend | transcribe <audio_file>
python cli.py free-models {groq|openrouter|nvidia}
python cli.py web backend | search "<query>"
python cli.py model-perf [--task analyze|plan|code|review]
python cli.py tune status | run <task_type> | auto
python cli.py slack list | approve <id> | skip <id>
```

## OpenAI-compatible HTTP shim

`python -m api.server` exposes the router as a standard OpenAI
chat-completions endpoint so any tool that speaks that wire format
(LangChain, glass overlay, desktop assistants, plain `curl`,
obsidian-copilot, the bundled SPA) can use the rotation + accounting
layer.

```bash
pip install fastapi 'uvicorn[standard]'
python -m api.server                          # 127.0.0.1:8765
API_HOST=0.0.0.0 API_PORT=8765 \
  API_COMPANY_TOKEN=mysecret \
  python -m api.server                        # public, requires Authorization: Bearer mysecret
```

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/`                              | Serves the SPA (`frontend/index.html` + `app.js`) |
| GET  | `/health`                        | Liveness |
| GET  | `/v1/metrics`                    | Prometheus metrics |
| GET  | `/v1/models`                     | Virtual + real model list |
| GET  | `/v1/accounting?workflow_id=…`   | Token + cost report |
| POST | `/v1/chat/completions`           | OpenAI-compatible (`stream:true` → SSE) |
| POST   | `/v1/workflows/start`            | Kick off a LangGraph workflow; returns `{workflow_id, ...}` |
| POST   | `/v1/workflows/{wid}/resume`     | Resume after a checkpoint (`{approved: true|false, reason?, raise_to?}`) |
| GET    | `/v1/workflows`                  | List all active workflows (LRU-capped, set `API_WORKFLOW_CACHE_MAX`) |
| GET    | `/v1/workflows/{wid}`            | Workflow status + latest interrupt payload |
| DELETE | `/v1/workflows/{wid}`            | Evict a workflow from cache |

The `model` field on `/v1/chat/completions` accepts:
- a task type: `analyze | plan | code | review | summarize | simple`
- `provider:model_id` for an explicit pick (e.g. `groq:llama-3.3-70b-versatile`)
- a bare provider name (uses that provider's default)
- any other string — router hint-resolves or falls back

## Frontend (`frontend/`)

Shipped today is a minimal Claude-style SPA in vanilla JS:

- `frontend/index.html` — single page; loaded by the shim at `GET /`
- `frontend/app.js` (265 lines) — chat UI that streams from
  `POST /v1/chat/completions` with `stream:true`

**This is the file set Claude design should replace / extend.** The
backend contract it must keep talking to:
- `POST /v1/chat/completions` (streaming SSE in OpenAI chunk format)
- `POST /v1/workflows/start` + `GET /v1/workflows/{id}` +
  `POST /v1/workflows/{id}/resume` — for the plan/code/commit
  checkpoint flow with Approve/Reject buttons
- `GET /v1/accounting` — for a cost panel
- `GET /v1/metrics` — for ops dashboards

Streamlit dashboard (`ui/dashboard.py`) is the **reference UX**: chat
panel, narration toggle, voice mic, git panel with one-click revert,
queue tab, memory search tab, model picker sidebar, accounting tab.

## Page Assistant widget

The SPA (`frontend/index.html`) embeds the [page-assistant](https://github.com/philipposk/page-assistant) floating widget (v0.2) — a grounded, voice-capable in-app helper that calls real AI-OS capabilities.

### How it works

1. The bundle (`frontend/page-assistant.global.js`) is built from `packages/widget` in the page-assistant monorepo and copied here.
2. The widget's grounding loop calls **`POST /v1/llm/complete`** (same origin) — API keys never leave the server.
3. Auth: the widget reads the `aios_token` cookie and passes it as `Authorization: Bearer <token>` on every round-trip. Set `API_COMPANY_TOKEN` on the server to require it.

### Updating the bundle

```bash
cd /path/to/page-assistant
git pull origin main
npm install
npm run build
cp packages/widget/dist/page-assistant.global.js /path/to/AI-OS/frontend/page-assistant.global.js
```

### Env vars for the widget bridge

| Variable | Purpose | Default |
|---|---|---|
| `API_COMPANY_TOKEN` | Bearer token for all AI-OS API endpoints (including `/v1/llm/complete`). Leave unset to deny all or set to `""` for open access. | (deny) |
| `PA_LLM_BASE_URL` | Override LLM base URL for the widget bridge | auto-detect |
| `PA_LLM_API_KEY` | Override API key for the widget bridge | auto-detect |
| `PA_LLM_MODEL` | Override model for the widget bridge | provider default |

Provider auto-detection order: `PA_LLM_*` → `OPENROUTER_API_KEY` → `GROQ_API_KEY` → `OPENAI_API_KEY`.

### Built-in capabilities

| Capability | Description |
|---|---|
| `check_health` | Checks backend liveness + available providers |
| `list_workflows` | Lists active workflows in the current session (requires `aios_token` cookie) |

### New in v0.2 (upstream changes merged 2026-06-30)

- **Voice settings UI** — gear icon opens a built-in voice picker (ElevenLabs / OpenAI / browser TTS, Whisper / browser STT). No custom UI needed.
- **Read-aloud** — `autoSpeak: true` makes the widget read replies aloud; user-controlled via voice settings.
- **`authToken` in init** — bearer token is now sent on every LLM proxy round-trip (not just capability calls), fixing auth on protected servers.
- **Phone launcher** — `open_page_link` can now launch `tel:` URIs (with confirm gate).
- **Agent discovery docs** — `AGENTS.md`, `INTEGRATION.md`, `SECURITY.md` added to upstream repo.

## Voice

- **STT** — Groq Whisper by default, OpenAI Whisper if `OPENAI_API_KEY`
  set, `whisper.cpp` local if installed. Backend introspection via
  `cli.py voice backend`. Implementation in
  [router/transcription.py](router/transcription.py).
- **TTS** — browser `SpeechSynthesis` from the dashboard's narration
  toggle. No server-side TTS dependency.
- **Per-checkpoint voice notes** — Slack/Telegram bots attach an MP3 of
  the checkpoint summary plus a PNG screenshot of the dashboard at that
  step (see [communication/media.py](communication/media.py)).

## Slack bot

Socket Mode bot in [communication/slack.py](communication/slack.py).

Two activation paths:

1. **Explicit slash command** — `/ai-run <task>` starts a workflow in a
   thread; each checkpoint posts a Block Kit message with Approve /
   Reject buttons + PNG + voice MP3.
2. **Passive ticket detection** — the bot subscribes to channel
   `message` events, classifies each one via
   [communication/ticket_detector.py](communication/ticket_detector.py)
   (uses the cheap `simple` task type), and when a message looks like a
   coding task posts a "🎫 Ticket detected — start workflow?" prompt
   with Start / Skip buttons. Approval kicks off the full plan → code →
   test → commit flow.

Persistent pending-tickets queue ([storage/slack_tickets.py](storage/slack_tickets.py))
survives restarts; dedup cache stops the same Slack message starting
two workflows; thread context is injected into `do_analyze`; an
optional approver gate (`SLACK_APPROVER_USER_IDS`) means only listed
users can press Start.

```
SLACK_AUTO_CHANNELS=C0123,C0456          # channel IDs to auto-scan
SLACK_AUTO_MENTIONS_ALWAYS=true          # also classify when bot is @mentioned anywhere
TICKET_CONFIDENCE_MIN=0.6                # below this, treat as non-ticket
TICKET_MAX_INPUT_CHARS=1200              # trim long messages before classify
SLACK_APPROVER_USER_IDS=U0123,U0456      # only these users see Start buttons
```

Required Slack app scopes for passive mode: `channels:history`,
`groups:history`, `chat:write`, `commands`, plus `message.channels` /
`message.groups` event subscriptions in Socket Mode.

Manage pending tickets without Slack: `python cli.py slack list | approve <id> | skip <id>`.

## Telegram bot

`python -m communication.telegram` — long-polling bot ([communication/telegram.py](communication/telegram.py)).

- `/ai_run <task>` — starts a workflow in the chat
- Each checkpoint sends an inline keyboard (Approve / Reject) plus PNG
  + voice MP3
- Same ticket-detection + pending-queue model as Slack
- Env: `TELEGRAM_BOT_TOKEN=…`,
  `TELEGRAM_APPROVER_USER_IDS=…` (optional)

## Email ingest

`python -m communication.email_ingest` — polls an IMAP mailbox, runs
each new message through the ticket detector, queues matches as
pending tickets. Configure via `EMAIL_IMAP_HOST`, `EMAIL_USER`,
`EMAIL_PASSWORD`, `EMAIL_MAILBOX` (default `INBOX`).

## Obsidian integration

Two-way:

- **Vault → ai_company.** Set `OBSIDIAN_VAULT_PATH=...`; every
  workflow's `do_analyze` automatically searches your vault for
  relevant notes and injects the top hits as context. `memory.add()`
  mirrors all stored notes into
  `<vault>/ai_company/<workflow_id>/<id-slug>.md` with YAML
  frontmatter ([storage/obsidian.py](storage/obsidian.py)).
- **ai_company → Obsidian Copilot.** Point the
  [logancyang/obsidian-copilot](https://github.com/logancyang/obsidian-copilot)
  plugin at the FastAPI shim (`http://localhost:8765/v1`). Step-by-step:
  [docs/obsidian-copilot-setup.md](docs/obsidian-copilot-setup.md).

CLI helpers: `cli.py obsidian status | export | search "<query>"`.

## Web search (Phase V/W)

`do_plan` can optionally pre-pull web context. Cascade order, first
that has a key wins: **Brave → Serper → Tavily → DuckDuckGo** (DDG
needs no key). Env vars: `BRAVE_SEARCH_API_KEY`, `SERPER_API_KEY`,
`TAVILY_API_KEY`. Run-time toggle: `cli.py run ... --search`. Manual:
`cli.py web search "<query>"`.

## Multi-agent crew

[orchestrator/crew/](orchestrator/crew/) ships role-specialised
sub-agents: Planner, Critic, Reviewer, Tester (plus an OpenHands role
hook). Enabled with `--crew` on `cli.py run` or `cli.py review-pr`.
Bus + coordinator in [orchestrator/crew/coordinator.py](orchestrator/crew/coordinator.py).

## PR review (CodeRabbit-style)

`cli.py review-pr <num|url>` posts a structured review on a GitHub PR
using `gh`. Flags:

- `--crew` — Reviewer + Critic crew with cross-check
- `--inline` — post inline comments with fix suggestions
- `--severity-threshold low|medium|high` — drop findings below threshold
- `--dry-run` — print, don't post

Runs in CI via [.github/workflows/pr-review.yml](.github/workflows/pr-review.yml).

## Self-improvement loop (Phase Z / Z2)

After every commit, `do_retrospective` writes a row to
[storage/retrospectives.py](storage/retrospectives.py) — verdict, cost,
test retries, plus a one-line LLM self-critique. That history feeds two
auto-pick layers:

```
workflow runs
  ↓ retrospective row (verdict + cost + retries)
  ↓ joined with ledger rows by workflow_id
  ↓ storage.model_performance.best_model_for(task_type)   ← USE_LEARNED_MODELS=1
  ↓ tuning.auto_tune (cron) → tuning/learned_prompts.json ← USE_LEARNED_PROMPTS=1
  ↓ next workflow uses tuned prompts + learned models
```

```bash
python cli.py tune status            # retrospective counts + on-disk learned prompts
python cli.py tune run --task-type plan
python cli.py tune auto              # cron-friendly: tune any task_type that crossed threshold
python cli.py model-perf             # per-(provider, model, task_type) success rates
python cli.py model-perf --best      # winner per task_type
```

### Activation env flags

Every new layer is **default-off**. Set the flag(s) you want, restart.
Each layer falls back to the legacy path if its dep or LLM call fails.

| Flag | What it does |
|---|---|
| `LANGGRAPH_CHECKPOINT_DB=./data/langgraph.sqlite` | Persistent workflow state — survives process restart (langgraph-checkpoint-sqlite) |
| `USE_LEARNED_PROMPTS=1` + `LEARNED_PROMPTS_PATH=…` | Plan/analyze/review nodes read tuned prompts from JSON instead of hardcoded strings |
| `USE_LEARNED_MODELS=1` + `BEST_MODEL_MAX_COST_PER_CALL=0.05` | `router.resolve()` auto-picks the highest-success (provider, model) from retrospective history |
| `AUTO_TUNE_MIN_TOTAL=20` + `AUTO_TUNE_MIN_NEW=10` + `AUTO_TUNE_STATE_PATH=…` | Threshold/watermark for `cli.py tune auto`. Atomic JSON writes — concurrent cron-safe |
| `LITELLM_PRIMARY=1` + `LITELLM_DEFAULT_MODEL=groq/llama-3.3-70b-versatile` + `LITELLM_TASK_MODEL_<TASK>=…` | Litellm becomes default provider for any task_type lacking an explicit `ROUTER_MODEL_<TASK>` pin |
| `USE_STRUCTURED_OUTPUTS=1` + `STRUCTURED_MODEL=…` | Plan + review nodes use instructor + pydantic for typed outputs (no more JSON-repair fallbacks) |

Cron example:
```cron
0 3 * * * cd /opt/ai-company && .venv/bin/python cli.py tune auto >> data/tune.log 2>&1
```

## Production hardening

- **Structured JSON logs** ([observability/logging.py](observability/logging.py))
  for any non-TTY environment. Toggle: `LOG_FORMAT=json|text`.
- **Prometheus metrics** ([observability/metrics.py](observability/metrics.py))
  at `GET /v1/metrics`. Per-route latency, per-provider request count
  + cost + token totals, queue depth, circuit-breaker state.
- **Optional Sentry** ([observability/sentry.py](observability/sentry.py))
  — set `SENTRY_DSN=…` to enable; no-op otherwise.

## Deploy

[infrastructure/](infrastructure/) ships Terraform modules for **six**
clouds (Phase Y):

- `infrastructure/oracle/` — Always-Free Ampere A1
- `infrastructure/aws/`
- `infrastructure/gcp/`
- `infrastructure/azure/`
- `infrastructure/hetzner/`
- `infrastructure/digitalocean/`

Common cloud-init in [infrastructure/common/](infrastructure/common/)
clones the repo into `/opt/ai-company`, builds a venv, installs
`ai-company.service` systemd unit. SSH-only by default; flip
`expose_streamlit_publicly = true` to open :8501 to the world (don't,
in production).

Oracle example:

```bash
cd infrastructure/oracle
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars
terraform init && terraform plan && terraform apply
ssh -L 8501:127.0.0.1:8501 opc@$(terraform output -raw public_ip)
```

## Docker

```bash
docker compose up --build
# dashboard on http://localhost:8501, ollama on http://localhost:11434
```

## Repository layout

```
ai_company/
├── cli.py                          # all sub-commands (see CLI section)
├── orchestration.py                # back-compat shim → orchestrator
├── orchestrator/
│   ├── graph.py                    # LangGraph StateGraph
│   ├── nodes.py                    # do_analyze / do_plan / do_code / do_test / do_review / do_commit
│   ├── checkpoints.py              # plan / code / commit / budget_exceeded interrupts
│   ├── budget.py                   # per-run + per-day USD caps
│   ├── structured.py               # JSON-schema response parsing
│   ├── state.py                    # GraphState TypedDict
│   └── crew/                       # roles + coordinator + bus (Planner/Critic/Reviewer/Tester/OpenHands)
├── router/
│   ├── router.py                   # ModelRouter (task_type → provider+model, fallback chain)
│   ├── rotation.py                 # free-model rotation per provider
│   ├── circuit.py                  # per-provider circuit breaker
│   ├── vision.py                   # image-block routing
│   ├── transcription.py            # Whisper STT (Groq / OpenAI / whisper.cpp)
│   ├── costs.py                    # per-million-token USD prices
│   ├── base.py                     # BaseProvider + ChatResult
│   ├── _sse.py                     # SSE chunk helpers
│   ├── litellm_client.py           # generic LiteLLM bridge
│   └── {anthropic,openrouter,groq,nvidia,ollama}_client.py
│       + {groq,openrouter,nvidia}_free.py   # live free-model lists
├── api/
│   └── server.py                   # FastAPI OpenAI-compat shim + /v1/workflows + SPA mount
├── frontend/
│   ├── index.html                  # minimal Claude-style SPA
│   └── app.js                      # streams /v1/chat/completions  ← what design should rebuild
├── ui/
│   └── dashboard.py                # Streamlit reference UX (full feature set)
├── communication/
│   ├── slack.py                    # Socket Mode bot + ticket detection
│   ├── telegram.py                 # long-polling bot + ticket detection
│   ├── email_ingest.py             # IMAP poll → ticket queue
│   ├── ticket_detector.py          # classifier
│   └── media.py                    # PNG screenshot + voice MP3 generation
├── tools/
│   ├── file_editor.py              # apply_plan via structured-JSON model response
│   └── git_ops.py                  # status / diff / commit + gh PR/issue helpers
├── storage/
│   ├── db.py                       # shared SQLite connection
│   ├── queue.py                    # priority task queue
│   ├── memory.py                   # FTS5 + semantic search
│   ├── embeddings.py               # Ollama / sentence-transformers / none
│   ├── accounting.py               # token+cost ledger
│   ├── retrospectives.py           # per-workflow outcomes (feeds tuning)
│   ├── model_performance.py        # per-(provider,model,task) success rates
│   ├── slack_tickets.py            # persistent pending Slack tickets
│   └── obsidian.py                 # vault mirror
├── tuning/
│   ├── tuner.py                    # DSPy MIPROv2 prompt optimiser
│   └── auto.py                     # cron-friendly threshold-based tune
├── observability/
│   ├── logging.py                  # JSON logs
│   ├── metrics.py                  # Prometheus
│   └── sentry.py                   # optional
├── infrastructure/
│   ├── deploy.sh
│   ├── common/                     # shared cloud-init + systemd unit
│   ├── modules/
│   └── {oracle,aws,gcp,azure,hetzner,digitalocean}/
├── tests/                          # 48 test files, 328 tests (308 pass; 18 skip on optional deps)
├── docs/
│   ├── glass-setup.md
│   ├── obsidian.md
│   └── obsidian-copilot-setup.md
├── examples/
│   └── scratch.py                  # live workflow smoke target
├── Dockerfile, docker-compose.yml, .dockerignore
├── .env.example
├── pytest.ini
└── requirements.txt
```

## Testing

```bash
pytest -q                                 # 328 tests; 308 pass (18 need optional deps)
pytest tests/test_graph.py -v             # orchestrator integration with stubs
pytest tests/test_providers.py -v         # per-provider request/response shape
pytest tests/test_api_server.py -v        # FastAPI shim + /v1/workflows
```

All tests use stubs / monkey-patched HTTP / SDK mocks. **Zero live LLM
calls in CI.** `cli.py check --live` is the live smoke-test.

## Status & known limits

- **Live LLM verification is your job once a key is configured.** Repo
  ships with no working key; tests pass on stubs.
- **File editor uses structured JSON, not Anthropic tool-use.**
  Trade-off: works with every provider in the table above, including
  free ones; less precise than tool-use for large multi-file edits.
- **Memory** supports semantic search via Ollama (`nomic-embed-text`)
  or `sentence-transformers` (`all-MiniLM-L6-v2`); falls back to FTS5
  keyword search when neither is available. Choose with
  `EMBEDDINGS_BACKEND={ollama|sentence-transformers|none}`. Inspect
  with `cli.py memory backend`; backfill with `cli.py memory reembed`.
- **`pi-nvidia-nim` submodule was removed** — targeted a different
  runtime (pi-coding-agent / TypeScript) and was never imported by the
  Python code.
- **Frontend handoff in progress.** `frontend/` ships the minimal SPA;
  the Streamlit dashboard is the reference UX. Backend HTTP contract
  is stable (see HTTP shim endpoints table).
