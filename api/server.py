"""OpenAI-compatible API shim over ModelRouter.

Lets external tools (LangChain, glass, Jarvis-style desktop assistants, any
SDK that speaks the OpenAI chat-completions wire format) reuse our free-tier
rotation + accounting + memory without knowing about LangGraph.

Endpoints
    GET  /v1/models                  list "virtual" models (task types + provider:model)
    POST /v1/chat/completions        chat (stream=true returns SSE)
    GET  /health                     plain 200 OK
    GET  /v1/accounting              token+cost ledger summary

Auth
    Optional bearer auth: set $API_COMPANY_TOKEN. If unset, the server is
    open. With a token set, every request must carry `Authorization: Bearer <token>`.

Run
    pip install "fastapi" "uvicorn[standard]"
    python -m api.server                  # binds 127.0.0.1:8765 by default
    API_HOST=0.0.0.0 API_PORT=8765 python -m api.server

Model id mapping
    The OpenAI clients send `model="gpt-4o-mini"` (or similar). We treat the
    string as either:
      - a "task type" we recognise (analyze / plan / code / review / simple)
      - a "provider:model" override (e.g. "groq:llama-3.3-70b-versatile")
      - a bare provider name to use that provider's default ("groq")
      - any other string → passed through to the resolver as-is
    Default: "simple".
"""

import asyncio
import hmac
import json
import logging
import os
import queue
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.requests import Request
    from fastapi.responses import JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover - exercised only without fastapi installed
    _HAS_FASTAPI = False

from router import ModelRouter
from router.base import ChatResult, ProviderUnavailable
from storage.accounting import report as accounting_report

logger = logging.getLogger(__name__)

_KNOWN_TASK_TYPES = {"analyze", "plan", "code", "review", "summarize", "test", "simple"}


def _auth_check(headers: Dict[str, str]) -> Optional[str]:
    """Return None (allowed) or an error string (denied).

    When API_COMPANY_TOKEN is unset the server DENIES all requests — open
    access is an opt-in, not the default. Set API_COMPANY_TOKEN='' explicitly
    in .env only if you intentionally want an open server.
    The bare /health endpoint is always allowed (checked at call site).
    """
    token = os.getenv("API_COMPANY_TOKEN")
    if token is None:
        # Token env var not set at all → deny by default
        return "unauthorised: API_COMPANY_TOKEN not configured"
    if token == "":
        # Explicitly empty string → operator chose open access
        return None
    got = headers.get("authorization") or headers.get("Authorization") or ""
    if got.startswith("Bearer "):
        provided = got[len("Bearer "):].strip()
        if hmac.compare_digest(provided.encode(), token.encode()):
            return None
    return "unauthorised"


def _resolve(model_id: str, router: ModelRouter) -> Tuple[str, Optional[str]]:
    """Map an inbound `model` string to (task_type, optional_explicit_model)."""
    if not model_id:
        return "simple", None
    if model_id in _KNOWN_TASK_TYPES:
        return model_id, None
    if ":" in model_id:
        provider, _, _sub = model_id.partition(":")
        if provider in router.providers:
            return "simple", model_id
    if model_id in router.providers:
        return "simple", router.providers[model_id].default_model()
    return "simple", model_id


def _to_openai_response(res: ChatResult) -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{int(time.time()*1000):x}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": res.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": res.text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
            "total_tokens": res.total_tokens,
        },
        "x_ai_company": {"provider": res.provider},
    }


async def _sse(router, messages, task_type, model_override, max_tokens, temperature,
               workflow_id: Optional[str] = None):
    cid = f"chatcmpl-{int(time.time()*1000):x}"
    created = int(time.time())
    pipe: queue.Queue = queue.Queue()
    SENTINEL = object()

    def _producer():
        try:
            for item in router.chat_stream(messages, task_type, model_override, max_tokens, temperature,
                                           workflow_id=workflow_id):
                pipe.put(item)
        except Exception as e:  # noqa: BLE001
            logger.error("SSE producer error task=%s: %s", task_type, e, exc_info=True)
            pipe.put(e)
        finally:
            pipe.put(SENTINEL)

    asyncio.get_running_loop().run_in_executor(None, _producer)

    yield "data: " + json.dumps({
        "id": cid, "object": "chat.completion.chunk", "created": created, "model": task_type,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }) + "\n\n"

    last_model = task_type
    while True:
        item = await asyncio.to_thread(pipe.get)
        if item is SENTINEL:
            break
        if isinstance(item, Exception):
            # Send a generic client message; the full error is already server-logged above
            yield "data: " + json.dumps({"error": {"message": "stream error", "type": "stream_error"}}) + "\n\n"
            break
        if isinstance(item, ChatResult):
            last_model = item.model
            yield "data: " + json.dumps({
                "id": cid, "object": "chat.completion.chunk", "created": created, "model": last_model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": item.prompt_tokens,
                    "completion_tokens": item.completion_tokens,
                    "total_tokens": item.total_tokens,
                },
                "x_ai_company": {"provider": item.provider},
            }) + "\n\n"
            continue
        yield "data: " + json.dumps({
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": last_model,
            "choices": [{"index": 0, "delta": {"content": item}, "finish_reason": None}],
        }) + "\n\n"

    yield "data: [DONE]\n\n"


def _build_app():
    if not _HAS_FASTAPI:
        raise RuntimeError("FastAPI not installed. `pip install fastapi 'uvicorn[standard]'`")

    app = FastAPI(title="ai_company OpenAI-compat shim", version="0.1.0")

    # CORS: default to localhost-only; set API_CORS_ORIGINS=* only if you need
    # broad access (e.g. glass desktop app loading from file://).
    origins_raw = os.getenv("API_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").strip()
    origins = [o.strip() for o in origins_raw.split(",") if o.strip()] or ["http://localhost:3000"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    router = ModelRouter()

    # Initialise observability hooks (no-op if envs not set / deps missing)
    try:
        from observability import init_metrics, init_sentry
        init_metrics()
        init_sentry()
    except Exception as e:  # noqa: BLE001
        logger.warning("observability init failed: %s", e)

    @app.get("/health")
    def health() -> Dict[str, Any]:
        # /health is always open — no auth required
        return {"ok": True, "providers": router.available_providers()}

    @app.get("/v1/metrics")
    def metrics_endpoint(request: Request):
        if (err := _auth_check(dict(request.headers))):
            raise HTTPException(status_code=401, detail=err)
        from fastapi.responses import Response
        from observability.metrics import metrics_render
        payload, content_type = metrics_render()
        return Response(content=payload, media_type=content_type)

    @app.get("/v1/models")
    def list_models(request: Request) -> Dict[str, Any]:
        if (err := _auth_check(dict(request.headers))):
            raise HTTPException(status_code=401, detail=err)
        now = int(time.time())
        out: List[Dict[str, Any]] = []
        for t in sorted(_KNOWN_TASK_TYPES):
            out.append({"id": t, "object": "model", "created": now, "owned_by": "ai_company.task"})
        for name, p in router.providers.items():
            if p.is_available():
                out.append({"id": f"{name}:{p.default_model()}", "object": "model", "created": now, "owned_by": name})
        return {"object": "list", "data": out}

    @app.get("/v1/accounting")
    def accounting(request: Request, workflow_id: Optional[str] = None) -> Dict[str, Any]:
        if (err := _auth_check(dict(request.headers))):
            raise HTTPException(status_code=401, detail=err)
        return accounting_report(workflow_id=workflow_id)

    # ---------- Workflows API (used by the bundled SPA + Slack/TG bots) ----------

    _WORKFLOW_MAX = int(os.getenv("API_WORKFLOW_CACHE_MAX", "500"))
    _workflows: "OrderedDict[str, Any]" = OrderedDict()  # workflow_id → entry; LRU eviction

    def _get_workflow(wid: str):
        # Lazy build because importing orchestrator pulls langgraph (heavy).
        from orchestrator import build_graph
        entry = _workflows.get(wid)
        if entry is None:
            entry = {"graph": build_graph(), "config": {"configurable": {"thread_id": wid}},
                     "pending": None, "created_at": time.time()}
            _workflows[wid] = entry
            # Evict oldest entries when over cap
            while len(_workflows) > _WORKFLOW_MAX:
                _workflows.popitem(last=False)
        else:
            # Touch → move to end (most-recently-used)
            _workflows.move_to_end(wid)
        return entry

    def _serialise_event(ev: dict) -> dict:
        out: Dict[str, Any] = {}
        for k, v in ev.items():
            if k == "__interrupt__":
                # Resolve interrupt objects to their values
                out["interrupt"] = (v[0].value if v else None)
            else:
                out[k] = v
        return out

    @app.post("/v1/workflows/start")
    async def workflow_start(request: Request):
        if (err := _auth_check(dict(request.headers))):
            raise HTTPException(status_code=401, detail=err)
        body = await request.json()
        task = (body.get("task") or "").strip()
        if not task:
            raise HTTPException(status_code=400, detail="task required")
        crew_mode = bool(body.get("crew_mode") or False)
        search_enabled = bool(body.get("search_enabled") or False)
        from orchestrator import new_workflow_id
        # Always generate server-side — never trust caller-supplied workflow_id
        wid = new_workflow_id()
        entry = _get_workflow(wid)
        payload = {"task": task, "workflow_id": wid,
                   "crew_mode": crew_mode if crew_mode else None,
                   "search_enabled": search_enabled}
        events: List[dict] = []
        pending: Optional[dict] = None

        def _drive():
            nonlocal pending
            for ev in entry["graph"].stream(payload, config=entry["config"]):
                if "__interrupt__" in ev:
                    pending = ev["__interrupt__"][0].value if ev["__interrupt__"] else None
                    break
                events.append(_serialise_event(ev))

        await asyncio.to_thread(_drive)
        entry["pending"] = pending
        return JSONResponse({"workflow_id": wid, "events": events, "pending": pending,
                             "finished": pending is None})

    @app.post("/v1/workflows/{wid}/resume")
    async def workflow_resume(wid: str, request: Request):
        if (err := _auth_check(dict(request.headers))):
            raise HTTPException(status_code=401, detail=err)
        body = await request.json()
        decision = body.get("decision")
        if decision is None:
            raise HTTPException(status_code=400, detail="decision required")
        entry = _get_workflow(wid)
        from langgraph.types import Command
        events: List[dict] = []
        pending: Optional[dict] = None

        def _drive():
            nonlocal pending
            for ev in entry["graph"].stream(Command(resume=decision), config=entry["config"]):
                if "__interrupt__" in ev:
                    pending = ev["__interrupt__"][0].value if ev["__interrupt__"] else None
                    break
                events.append(_serialise_event(ev))

        await asyncio.to_thread(_drive)
        entry["pending"] = pending
        return JSONResponse({"workflow_id": wid, "events": events, "pending": pending,
                             "finished": pending is None})

    @app.get("/v1/workflows")
    def list_workflows(request: Request):
        if (err := _auth_check(dict(request.headers))):
            raise HTTPException(status_code=401, detail=err)
        now = time.time()
        return {"workflows": [
            {"workflow_id": wid, "pending": e.get("pending"),
             "age_s": round(now - e.get("created_at", now), 1)}
            for wid, e in list(_workflows.items())
        ]}

    @app.get("/v1/workflows/{wid}")
    def workflow_status(wid: str, request: Request):
        if (err := _auth_check(dict(request.headers))):
            raise HTTPException(status_code=401, detail=err)
        entry = _workflows.get(wid)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown workflow")
        return {"workflow_id": wid, "pending": entry.get("pending")}

    @app.delete("/v1/workflows/{wid}")
    def delete_workflow(wid: str, request: Request):
        if (err := _auth_check(dict(request.headers))):
            raise HTTPException(status_code=401, detail=err)
        removed = _workflows.pop(wid, None)
        if removed is None:
            raise HTTPException(status_code=404, detail="unknown workflow")
        return {"workflow_id": wid, "evicted": True}

    # ---------- Page-assistant LLM bridge ----------
    # Accepts the @page-assistant/widget grounding-loop format and proxies it to the
    # first available OpenAI-compatible provider so the widget works against AI-OS
    # without exposing API keys to the browser.

    def _pa_provider() -> Tuple[str, str, str]:
        """(base_url, api_key, model) for the page-assistant bridge.

        Priority: explicit PA_LLM_* envs → OPENROUTER_API_KEY → GROQ_API_KEY → OPENAI_API_KEY.
        Model defaults to a capable-but-cheap tool-calling model on each provider.
        """
        pa_url = os.getenv("PA_LLM_BASE_URL")
        pa_key = os.getenv("PA_LLM_API_KEY")
        pa_model = os.getenv("PA_LLM_MODEL")
        if pa_url and pa_key:
            return pa_url.rstrip("/"), pa_key, pa_model or "gpt-4o-mini"

        or_key = os.getenv("OPENROUTER_API_KEY")
        if or_key:
            return "https://openrouter.ai/api/v1", or_key, pa_model or "anthropic/claude-3.5-haiku"

        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            return "https://api.groq.com/openai/v1", groq_key, pa_model or "llama-3.1-8b-instant"

        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            return "https://api.openai.com/v1", openai_key, pa_model or "gpt-4o-mini"

        raise ProviderUnavailable(
            "No LLM provider for /v1/llm/complete — set OPENROUTER_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY"
        )

    def _pa_safe_parse(s: str) -> Dict[str, Any]:
        try:
            return json.loads(s)
        except Exception:  # noqa: BLE001
            return {}

    @app.post("/v1/llm/complete")
    async def llm_complete(request: Request):
        """Page-assistant widget LLM bridge.

        Translates the widget's tool-calling round-trip format (LLMCompletionInput →
        LLMCompletionOutput) into a standard OpenAI chat-completions request so the
        page-assistant widget can use AI-OS as its backend without exposing any keys
        to the browser.

        Auth: same bearer token as every other AI-OS endpoint (API_COMPANY_TOKEN).
        """
        if (err := _auth_check(dict(request.headers))):
            raise HTTPException(status_code=401, detail=err)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid json")

        system: str = body.get("system", "")
        pa_messages: List[Dict[str, Any]] = body.get("messages", [])
        pa_tools: List[Dict[str, Any]] = body.get("tools", [])
        force_tool: Optional[str] = body.get("forceTool")
        temperature: float = float(body.get("temperature", 0.3))

        # Convert page-assistant messages → OpenAI messages
        openai_messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        for m in pa_messages:
            role = m.get("role", "user")
            content = str(m.get("content", ""))
            if role == "tool":
                tool_name = m.get("toolName", "tool")
                openai_messages.append({"role": "user", "content": f"[result of {tool_name}]\n{content}"})
            else:
                openai_messages.append({"role": role, "content": content})

        # Convert page-assistant tools → OpenAI tools
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": {"type": "object", **t.get("parameters", {})},
                },
            }
            for t in pa_tools
        ]

        try:
            base_url, api_key, model = _pa_provider()
        except ProviderUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))

        req_body: Dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": openai_messages,
            "tools": openai_tools,
        }
        if force_tool:
            req_body["tool_choice"] = {"type": "function", "function": {"name": force_tool}}

        import httpx as _httpx
        try:
            async with _httpx.AsyncClient(timeout=60.0) as client:
                upstream = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
                    json=req_body,
                )
        except _httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"upstream request failed: {e}")

        if not upstream.is_success:
            raise HTTPException(status_code=502, detail=f"upstream {upstream.status_code}: {upstream.text[:300]}")

        data = upstream.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}

        tool_calls = [
            {"name": tc["function"]["name"], "args": _pa_safe_parse(tc["function"]["arguments"])}
            for tc in (msg.get("tool_calls") or [])
        ]
        text: str = (msg.get("content") or "").strip()

        return JSONResponse({"toolCalls": tool_calls, "text": text})

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        if (err := _auth_check(dict(request.headers))):
            raise HTTPException(status_code=401, detail=err)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid json")

        messages = body.get("messages") or []
        if not messages:
            raise HTTPException(status_code=400, detail="messages required")
        stream = bool(body.get("stream"))
        raw_max_tokens = body.get("max_tokens")
        raw_temperature = body.get("temperature")
        max_tokens = max(1, min(int(raw_max_tokens or 1024), 32768))
        temperature = max(0.0, min(float(raw_temperature if raw_temperature is not None else 0.7), 2.0))
        task_type, model_override = _resolve(body.get("model", ""), router)
        wf_id: Optional[str] = body.get("workflow_id") or None

        if stream:
            return StreamingResponse(
                _sse(router, messages, task_type, model_override, max_tokens, temperature,
                     workflow_id=wf_id),
                media_type="text/event-stream",
            )
        try:
            res = await asyncio.to_thread(
                router.chat, messages, task_type, model_override, max_tokens, temperature,
                workflow_id=wf_id,
            )
        except ProviderUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        return JSONResponse(_to_openai_response(res))

    # ---------- Static SPA (mounted last so it never shadows API routes) ----------
    from pathlib import Path as _Path
    from fastapi.responses import RedirectResponse
    _spa_dir = _Path(__file__).resolve().parent.parent / "frontend"
    if _spa_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=str(_spa_dir), html=True), name="spa")

        @app.get("/")
        def _root():
            return RedirectResponse(url="/ui/")

    return app


def get_app():
    return _build_app()


def main() -> int:
    try:
        import uvicorn
    except ImportError:
        print("Run: pip install fastapi 'uvicorn[standard]'")
        return 2
    # Optional structured logging — flip with LOG_FORMAT=json
    try:
        from observability import configure_logging
        configure_logging()
    except Exception:  # noqa: BLE001
        pass
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8765"))
    token_env = os.getenv("API_COMPANY_TOKEN")
    auth_status = "required" if token_env else ("open (API_COMPANY_TOKEN='')" if token_env == "" else "LOCKED (API_COMPANY_TOKEN unset — set to '' to open)")
    print(f"⚡ ai_company OpenAI shim on http://{host}:{port}  (auth: {auth_status})")
    uvicorn.run("api.server:get_app", host=host, port=port, factory=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
