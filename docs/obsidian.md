# Obsidian integration

Two independent ways `ai_company` plays with an Obsidian vault. They compose;
turn on either, both, or neither.

## A. Vault mirror (path b — already shipped)

Every `memory.add()` writes a markdown file with YAML frontmatter into your
vault, so Obsidian's own search + graph view sees the agent's analyses,
reviews, and notes. Implemented in [storage/obsidian.py](../storage/obsidian.py)
and wired into [orchestrator/nodes.py](../orchestrator/nodes.py)
(`analyze_node` augments its prompt with `obsidian.search_vault(task)` hits,
so manual notes you write in the vault influence agent analyses).

### Enable

Add to `.env`:

```
OBSIDIAN_VAULT_PATH=/Users/you/Vault            # absolute path to vault root
# OBSIDIAN_DISABLED=1                            # uncomment to turn off without unsetting the path
```

Restart the dashboard / CLI. Verify:

```bash
python -c "from storage.obsidian import vault_root; print(vault_root())"
```

### What lands in the vault

```
<vault>/
└── ai_company/
    └── <workflow_id-or-"misc">/
        └── <doc_id>-<slug>.md
```

Frontmatter:

```yaml
---
id: 17
kind: analysis           # analysis | review | note | ...
workflow_id: wf-2026-...
created_at: 2026-05-18T20:00:00Z
tags: [ai_company, kind/analysis]
task: "Add --version flag to cli.py"
files: [cli.py]
---
TASK: ...
ANALYSIS: ...
```

### Backfill existing memory

If you enable the vault after running tasks for a while:

```python
from storage.obsidian import bulk_export
bulk_export()                       # all kinds
bulk_export(kinds=["analysis"])     # subset
```

Idempotent — files already on disk are not re-written.

## B. Obsidian-Copilot → `api/server.py` (path a — zero code in this repo)

Route the popular Obsidian-Copilot plugin at the OpenAI-compat shim served
from [api/server.py](../api/server.py) so the same router (fallback chain,
free-tier rotation, accounting ledger) powers chat **inside Obsidian**.

### Steps

1. Run the shim:

   ```bash
   python -m api.server                          # binds 127.0.0.1:8765
   API_HOST=0.0.0.0 API_PORT=8765 API_COMPANY_TOKEN=mysecret python -m api.server
   ```

2. Install the [logancyang/obsidian-copilot](https://github.com/logancyang/obsidian-copilot)
   community plugin in Obsidian (Settings → Community plugins → Browse).

3. In the plugin's settings:

   | Field | Value |
   |---|---|
   | Provider | OpenAI (or "Custom" / "OpenAI-compatible") |
   | Base URL | `http://localhost:8765/v1` |
   | API key | value of `API_COMPANY_TOKEN` (any string if unset) |
   | Model | `simple` (or `analyze` / `plan` / `code` / `review` / a `provider:model_id`) |

4. Test from inside Obsidian: open the Copilot panel and ask anything. The
   request goes to `api/server.py`, which routes via `ModelRouter`, falls back
   across providers, and writes to `accounting`.

### Why this matters

- Use any free-tier provider from inside your vault without re-doing key
  management or rotation.
- `python cli.py accounting` shows the spend whether the call came from CLI,
  dashboard, Slack, Telegram, or Obsidian.
- The vault mirror (path A) + this shim (path B) compose: your conversations
  in Obsidian-Copilot can reference notes the agent has dropped in the vault.

### Troubleshooting

- `connection refused`: shim not running. `python -m api.server` first.
- `401`: plugin didn't send Authorization. Set the API key field in the
  plugin (any non-empty string works when `API_COMPANY_TOKEN` is unset).
- `model not found`: pick `simple`, or a task name from
  `analyze | plan | code | review | summarize`, or `provider:model_id` like
  `openrouter:meta-llama/llama-3.2-3b-instruct:free`.
- `GET /v1/models` lists every virtual model the shim accepts.
