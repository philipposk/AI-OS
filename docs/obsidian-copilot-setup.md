# Obsidian Copilot ↔ ai_company

Point the [logancyang/obsidian-copilot](https://github.com/logancyang/obsidian-copilot)
plugin at your local `ai_company` FastAPI shim so chats inside Obsidian
route through your free-tier rotation + accounting layer. Zero code on
this project's side.

## Prerequisites

1. **Obsidian** desktop installed, vault open.
2. **`ai_company` running**:
   ```bash
   cd "/Users/phktistakis/Devoloper Projects/AI OS/ai_company"
   .venv/bin/python -m api.server          # binds 127.0.0.1:8765
   ```
   To bind on the network (e.g. for Obsidian on another machine):
   ```bash
   API_HOST=0.0.0.0 API_PORT=8765 API_COMPANY_TOKEN=mysecret \
     .venv/bin/python -m api.server
   ```
3. **Verify the shim**:
   ```bash
   curl -s http://localhost:8765/health
   curl -s http://localhost:8765/v1/models | jq '.data | length'
   ```

## Install Obsidian Copilot

1. Obsidian → **Settings** → **Community plugins** → **Browse**
2. Search **Copilot** (by Logan Yang). Install. Enable.
3. **Settings** → **Copilot**:
   - **Default model** → pick **OpenAI** (any OpenAI-compatible)
   - **OpenAI API Key** → any string (or your `API_COMPANY_TOKEN` if set)
   - **Custom OpenAI base URL** → `http://localhost:8765/v1`
   - **Default chat model** → `simple`, `analyze`, `plan`, `code`,
     `review`, or `groq:llama-3.3-70b-versatile`, etc.
4. Open the Copilot chat panel (left ribbon icon) and ask anything.

## Mirror direction: ai_company → vault

Set `OBSIDIAN_VAULT_PATH` so every workflow note is mirrored into your vault:

```bash
export OBSIDIAN_VAULT_PATH="/Users/$USER/Obsidian/MyVault"
.venv/bin/python cli.py obsidian status     # confirm vault path detected
.venv/bin/python cli.py obsidian export     # backfill existing memory_docs
```

Each workflow run drops markdown into
`<vault>/ai_company/<workflow_id>/<id-slug>.md` with YAML frontmatter
(`id`, `kind`, `workflow_id`, `created_at`, `tags`, `task`, `files`).
Use Obsidian's graph + backlinks to navigate.

## Mirror direction: vault → ai_company

Already wired. `analyze_node` searches `$OBSIDIAN_VAULT_PATH` for
substring matches relevant to each task and injects the top 3 hits into
the analysis prompt. No setup beyond `OBSIDIAN_VAULT_PATH`.

## Pick a model strategy from inside Obsidian

The shim treats the `model` field as a task type or explicit override.
Useful values to type into Copilot's "Default chat model":

| Value | What it does |
|---|---|
| `simple` | Cheapest route. Rotates across free tiers. |
| `analyze` | Same as workflow analyze step (env-driven model). |
| `plan` | Same as workflow plan step. |
| `groq:llama-3.3-70b-versatile` | Pin Groq's fastest free 70B. |
| `openrouter:openai/gpt-oss-120b:free` | Pin OpenRouter's biggest free. |
| `nvidia:meta/llama-3.3-70b-instruct` | Pin NVIDIA's 70B (free build endpoint). |
| `claude-haiku-4-5` | Anthropic (only if `ANTHROPIC_API_KEY` set; paid). |

## Auth

If you set `API_COMPANY_TOKEN`, every Obsidian Copilot request must
carry it as a bearer token. Configure it as the **OpenAI API Key** in
Copilot's settings — Copilot already sends it as
`Authorization: Bearer <key>`.

## Streaming

Copilot's streaming UI works. The shim returns OpenAI-shape SSE chunks
when `stream:true` is sent (Copilot does this by default).

## Troubleshooting

- **Copilot says "API key invalid"** → your `API_COMPANY_TOKEN` doesn't
  match what Copilot sends. Clear both or sync them.
- **Empty responses** → check `cli.py providers` to confirm at least
  one provider is available; check `cli.py check --live` for working
  credentials.
- **High latency** → first call to OpenRouter free-rotate fetches the
  model list (TTL 1h). Re-cached after. `cli.py free-models refresh
  --provider openrouter` warms it manually.
- **Cost** → `cli.py accounting` shows total tokens + USD per provider
  for the day. Free tiers should stay at `$0.00`.

## Stop the shim

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN -t | xargs kill
```
