# ai_company

Semi-autonomous multi-agent orchestrator. You describe a task; a LangGraph
state machine plans it, edits files, runs tests, and stops at three
human-in-the-loop checkpoints (plan, code, commit) before anything is
committed.

```
User (CLI / Streamlit / Slack-soon)
        │
        ▼
LangGraph state machine (orchestrator/graph.py)
  do_analyze → do_plan → checkpoint_plan
                                ↓ approved
                              do_code → do_test → (passed?) → do_review
                                  ↑       ↓no, retries<3                ↓
                                  └── do_code_retry             checkpoint_commit
                                            ↓ retries≥3                 ↓ approved
                                       checkpoint_code              do_commit → END
        │
        ▼
ModelRouter (router/router.py)
  anthropic │ openrouter │ groq │ nvidia │ ollama   (whichever is configured)
        │
SQLite state (storage/)
  queue (push/pop/done/failed) + memory (FTS5) + accounting (token+cost ledger)
```

## Quick start (local Mac)

```bash
cd ai_company
./infrastructure/deploy.sh    # creates .venv + installs deps + .env from .env.example
source .venv/bin/activate
$EDITOR .env                  # add at least ONE provider key (see Providers below)
python cli.py check           # sanity-check
streamlit run ui/dashboard.py # http://localhost:8501
```

Or skip the UI and drive from the terminal:

```bash
python cli.py run "Add a --version flag to cli.py"
# walks each interrupt: review_plan → review_code (only if tests fail) → review_commit
```

## Providers

Any one of:

| Provider     | Env var               | Free tier? | Default model                              |
|--------------|-----------------------|-----------|--------------------------------------------|
| Anthropic    | `ANTHROPIC_API_KEY`   | no        | `claude-haiku-4-5` (cheapest Claude)       |
| OpenRouter   | `OPENROUTER_API_KEY`  | yes (`:free` tags) | `meta-llama/llama-3.2-3b-instruct:free` |
| NVIDIA NIM   | `NVCF_API_KEY`        | yes (build endpoints) | `meta/llama-3.1-8b-instruct` |
| Groq         | `GROQ_API_KEY`        | yes (dev tier) | `llama-3.3-70b-versatile`             |
| Ollama       | `OLLAMA_BASE_URL`     | local / free | `llama3.2:3b`                          |

Per-task overrides via env: `ROUTER_MODEL_ANALYZE`, `_PLAN`, `_CODE`,
`_REVIEW`. Force a model from the sidebar in the dashboard.

The router falls back across providers automatically. If you set
`ROUTER_MODEL_PLAN=claude-opus-4-7` but Anthropic isn't available, it falls
back to the next available provider's default model rather than crashing.

## CLI

```bash
python cli.py run "<task>"                # run a workflow with terminal HIL prompts
python cli.py providers                   # which providers have a key
python cli.py check                       # smoke-test the install
python cli.py accounting [--json]         # token + cost ledger summary
python cli.py queue push "<task>" [--priority N]
python cli.py queue list [--status …]
python cli.py queue status
python cli.py queue cancel <id>
python cli.py memory search "<query>"     # FTS5 over stored notes
python cli.py memory count
```

## Deploy to Oracle Cloud (Always-Free Ampere A1)

```bash
cd infrastructure
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars      # tenancy / user OCIDs, region, SSH pubkey path
terraform init
terraform plan
terraform apply
ssh -L 8501:127.0.0.1:8501 opc@$(terraform output -raw public_ip)
# Streamlit now on http://localhost:8501 on your laptop, tunnelled to the VM
```

The cloud-init script clones this repo into `/opt/ai-company`, builds a
venv, and installs a `ai-company.service` systemd unit. Default exposes only
SSH publicly; flip `expose_streamlit_publicly = true` if you want :8501 open
to the world (don't, in production).

## Docker

```bash
docker compose up --build
# dashboard on http://localhost:8501, ollama on http://localhost:11434
```

## Obsidian integration

Two-way:

- **Vault → ai_company.** Set `OBSIDIAN_VAULT_PATH=...`; every workflow's
  analyze node automatically searches your vault for relevant notes and
  injects the top hits as context. `memory.add()` mirrors all stored
  notes into `<vault>/ai_company/<workflow_id>/<id-slug>.md` with YAML
  frontmatter.
- **ai_company → Obsidian Copilot.** Point the
  [logancyang/obsidian-copilot](https://github.com/logancyang/obsidian-copilot)
  plugin at the FastAPI shim (`http://localhost:8765/v1`). Chats from
  inside Obsidian then flow through our free-tier rotation +
  accounting. Step-by-step: [docs/obsidian-copilot-setup.md](docs/obsidian-copilot-setup.md).

CLI helpers: `cli.py obsidian status | export | search "<query>"`.

## Repository layout

```
ai_company/
├── cli.py                 # user CLI (run / providers / check / queue / memory / accounting)
├── orchestration.py       # back-compat shim → orchestrator
├── orchestrator/
│   ├── graph.py           # LangGraph StateGraph
│   ├── nodes.py           # do_analyze / do_plan / do_code / do_test / do_review / do_commit
│   ├── checkpoints.py     # human-in-the-loop interrupts
│   └── state.py           # GraphState TypedDict
├── router/
│   ├── router.py          # ModelRouter (task_type → provider+model, fallback chain)
│   ├── base.py            # BaseProvider + ChatResult
│   ├── {anthropic,openrouter,groq,nvidia,ollama}_client.py
│   └── costs.py           # per-million-token USD prices
├── tools/
│   ├── file_editor.py     # apply_plan via structured-JSON model response
│   └── git_ops.py         # status / diff / commit helpers
├── storage/
│   ├── db.py              # shared SQLite connection
│   ├── queue.py           # priority task queue
│   ├── memory.py          # FTS5 over workflow notes
│   └── accounting.py      # token+cost ledger
├── ui/dashboard.py        # Streamlit dashboard
├── communication/slack.py # stub; Slack bot wiring lives here when added
├── infrastructure/        # Terraform + cloud-init + deploy.sh
├── tests/                 # 38 tests, no live LLM calls
├── Dockerfile, docker-compose.yml, .dockerignore
├── .env.example
└── requirements.txt
```

## OpenAI-compatible API shim

`python -m api.server` exposes the router as a standard OpenAI chat-completions
endpoint so any tool that speaks that wire format (LangChain, glass overlay,
desktop assistants, plain `curl`) can use our free-tier rotation and
accounting layer:

```bash
pip install fastapi 'uvicorn[standard]'
python -m api.server                    # 127.0.0.1:8765 by default
API_HOST=0.0.0.0 API_PORT=8765 \
  API_COMPANY_TOKEN=mysecret \
  python -m api.server                  # bind public, require Authorization: Bearer mysecret

curl -s http://localhost:8765/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"simple","messages":[{"role":"user","content":"hi"}]}'
```

The `model` field accepts:
- a task type: `analyze | plan | code | review | summarize | simple`
- `provider:model_id` for an explicit pick (e.g. `groq:llama-3.3-70b-versatile`)
- a bare provider name (uses that provider's default model)
- any other string — the router will hint-resolve or fall back

`stream:true` returns SSE in the OpenAI chunk format. `GET /v1/models` lists
virtual models. `GET /v1/accounting` returns the current token+cost report
(optionally filtered with `?workflow_id=…`).

## Dashboard narration

The Streamlit dashboard has a sidebar toggle **🔊 narrate workflow**. When on,
each finished node's output is spoken aloud by the browser as the workflow
runs. Plays nicely with the existing "🎙️ Voice chat" panel.

## Testing

```bash
pytest -q                                 # 38 tests, ~5s
pytest tests/test_graph.py -v             # orchestrator integration with stubs
pytest tests/test_providers.py -v         # per-provider request/response shape
```

All tests use stubs / monkey-patched HTTP / SDK mocks. Zero live LLM calls
in CI.

## Status & known limits

- **Live LLM verification is your job once a key is configured.** The repo
  ships with no working key; tests pass on stubs.
- **File editor uses structured JSON, not Anthropic tool-use.** Trade-off:
  works with every provider in the table above, including free ones; less
  precise than tool-use for large multi-file edits. Revisit if quality is
  thin.
- **Memory** supports semantic search via Ollama (`nomic-embed-text`) or
  `sentence-transformers` (`all-MiniLM-L6-v2`); falls back to FTS5
  keyword search when neither is available. Choose with
  `EMBEDDINGS_BACKEND={ollama|sentence-transformers|none}`. Use
  `python cli.py memory backend` to inspect, `memory reembed` to
  backfill old rows.
- **Slack bot** — Socket Mode bot in [communication/slack.py](communication/slack.py).
  `/ai-run <task>` starts a workflow in a thread; each checkpoint posts a
  Block Kit message with Approve/Reject buttons. Run with:
  ```
  pip install slack-bolt slack-sdk
  SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... \
    python -m communication.slack
  ```
- **`pi-nvidia-nim` submodule was removed** — it targeted a different
  runtime (pi-coding-agent / TypeScript) and was never imported by the
  Python code.
