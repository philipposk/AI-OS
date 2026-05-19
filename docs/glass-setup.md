# Glass ↔ ai_company

Point the [pickle-com/glass](https://github.com/pickle-com/glass) desktop
overlay at the local FastAPI shim so its on-screen chat + Cluely-style
overlays flow through your free-tier rotation + accounting.

Zero code on this project's side; glass speaks OpenAI-compat.

## Prerequisites

1. **`ai_company` shim running**:
   ```bash
   cd "/Users/phktistakis/Devoloper Projects/AI OS/ai_company"
   .venv/bin/python -m api.server          # 127.0.0.1:8765
   ```
   Or with auth (recommended on a shared machine):
   ```bash
   API_COMPANY_TOKEN="$(openssl rand -hex 16)" .venv/bin/python -m api.server
   ```
2. **Sanity check**:
   ```bash
   curl -s http://localhost:8765/health
   ```

## Install glass

Build from source (Electron app):

```bash
git clone https://github.com/pickle-com/glass.git
cd glass
npm install
npm run build
npm start
```

## Point glass at the shim

Glass stores model config in its settings UI. Set:

| Field | Value |
|---|---|
| **Provider** | OpenAI-compatible |
| **API base URL** | `http://localhost:8765/v1` |
| **API key** | any string, or `$API_COMPANY_TOKEN` you set above |
| **Model** | `simple`, `analyze`, or `groq:llama-3.3-70b-versatile` |

That's it. Every chat glass sends now lands in our SSE handler, picks a
provider from the cascade, rotates free models, and writes to the cost
ledger.

## CORS

The shim ships with permissive CORS (`Access-Control-Allow-Origin: *`),
because glass runs as `app://` or `file://` in Electron and would
otherwise hit a preflight wall. Tighten by setting
`API_CORS_ORIGINS="app://*,http://localhost:5173"` if you want explicit
allow-list. Auth is still enforced via `API_COMPANY_TOKEN`.

## What works today

- Text chat (streaming, non-streaming)
- Model picker (every task type + provider:model id from `/v1/models`)
- Token + cost reporting per glass session via `/v1/accounting`

## What doesn't work yet

- glass's "watch my screen" feature streams frames to a vision model.
  Our shim does not implement `/v1/chat/completions` with image content
  yet (only text). When glass falls back to text, it just works.
- glass's voice mode uses its own STT/TTS. To unify with our voice
  pipeline (Groq Whisper STT + browser TTS), point glass at the bundled
  SPA at `http://localhost:8765/` instead.

## Stop the shim

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN -t | xargs kill
```
