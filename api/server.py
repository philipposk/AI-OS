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
import json
import logging
import os
import queue
import time
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
    token = os.getenv("API_COMPANY_TOKEN")
    if not token:
        return None
    got = headers.get("authorization") or headers.get("Authorization") or ""
    if got.startswith("Bearer ") and got.removeprefix("Bearer ").strip() == token:
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


async def _sse(router, messages, task_type, model_override, max_tokens, temperature):
    cid = f"chatcmpl-{int(time.time()*1000):x}"
    created = int(time.time())
    pipe: queue.Queue = queue.Queue()
    SENTINEL = object()

    def _producer():
        try:
            for item in router.chat_stream(messages, task_type, model_override, max_tokens, temperature):
                pipe.put(item)
        except Exception as e:  # noqa: BLE001
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
            yield "data: " + json.dumps({"error": {"message": str(item), "type": type(item).__name__}}) + "\n\n"
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

    # CORS: glass and the bundled SPA both load via different origins
    # (file://, app://, localhost ports). Allowed origins are env-driven; the
    # default of "*" is acceptable because we still gate on the optional
    # Authorization: Bearer header (API_COMPANY_TOKEN).
    origins_raw = os.getenv("API_CORS_ORIGINS", "*").strip()
    origins = [o.strip() for o in origins_raw.split(",") if o.strip()] or ["*"]
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
        return {"ok": True, "providers": router.available_providers()}

    @app.get("/v1/metrics")
    def metrics_endpoint():
        from fastapi.responses import Response
        from observability.metrics import metrics_render
        payload, content_type = metrics_render()
        return Response(content=payload, media_type=content_type)

    @app.get("/v1/models")
    def list_models() -> Dict[str, Any]:
        now = int(time.time())
        out: List[Dict[str, Any]] = []
        for t in sorted(_KNOWN_TASK_TYPES):
            out.append({"id": t, "object": "model", "created": now, "owned_by": "ai_company.task"})
        for name, p in router.providers.items():
            if p.is_available():
                out.append({"id": f"{name}:{p.default_model()}", "object": "model", "created": now, "owned_by": name})
        return {"object": "list", "data": out}

    @app.get("/v1/accounting")
    def accounting(workflow_id: Optional[str] = None) -> Dict[str, Any]:
        return accounting_report(workflow_id=workflow_id)

    # ---------- Workflows API (used by the bundled SPA + Slack/TG bots) ----------

    _workflows: Dict[str, Any] = {}  # workflow_id → (graph, config, last_pending)

    def _get_workflow(wid: str):
        # Lazy build because importing orchestrator pulls langgraph (heavy).
        from orchestrator import build_graph
        entry = _workflows.get(wid)
        if entry is None:
            entry = {"graph": build_graph(), "config": {"configurable": {"thread_id": wid}},
                     "pending": None}
            _workflows[wid] = entry
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
        wid = body.get("workflow_id") or new_workflow_id()
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

    @app.get("/v1/workflows/{wid}")
    def workflow_status(wid: str):
        entry = _workflows.get(wid)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown workflow")
        return {"workflow_id": wid, "pending": entry.get("pending")}

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
        max_tokens = int(body.get("max_tokens") or 1024)
        temperature = float(body.get("temperature") or 0.7)
        task_type, model_override = _resolve(body.get("model", ""), router)

        if stream:
            return StreamingResponse(
                _sse(router, messages, task_type, model_override, max_tokens, temperature),
                media_type="text/event-stream",
            )
        try:
            res = await asyncio.to_thread(
                router.chat, messages, task_type, model_override, max_tokens, temperature,
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
    print(f"⚡ ai_company OpenAI shim on http://{host}:{port}  (auth: {'on' if os.getenv('API_COMPANY_TOKEN') else 'off'})")
    uvicorn.run("api.server:get_app", host=host, port=port, factory=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
