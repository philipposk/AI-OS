"""Visual + audio rendering of checkpoint cards for chat-app posting.

Two outputs per checkpoint:

  render_checkpoint_image(payload, workflow_id) -> PNG bytes
    PIL-rendered card showing the same info the Slack/Telegram text
    message has but laid out as a single image (works in clients that
    don't render Block Kit / Markdown well, like push notifications).

  synthesize_voice(text) -> (mp3 bytes, mime, duration_seconds)
    Free server-side TTS via edge-tts (Microsoft Edge's neural voices,
    no API key). Returns (None, None, 0.0) if edge-tts isn't installed
    or the network is unreachable.

Optional deps (lazy-imported):
  pillow         (almost always already on the box; required for image)
  edge-tts       (optional; voice falls back to "no audio" if missing)
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ---------- image ----------

IMG_WIDTH = 1200
IMG_PAD = 40
LINE_HEIGHT = 26
TITLE_HEIGHT = 36

_BG = (24, 27, 32)
_FG = (230, 230, 230)
_MUTED = (160, 165, 170)
_ACCENT = (255, 196, 71)
_GREEN = (76, 175, 80)
_RED = (244, 67, 54)


def _font(size: int):
    from PIL import ImageFont
    # Try a sensible monospace; fall back to default if not found.
    for name in (
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "Menlo.ttc", "Monaco.ttf", "DejaVuSansMono.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _wrap(text: str, max_chars: int) -> list[str]:
    out: list[str] = []
    for paragraph in (text or "").splitlines():
        if not paragraph:
            out.append("")
            continue
        line = ""
        for word in paragraph.split(" "):
            if not word:
                continue
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= max_chars:
                line += " " + word
            else:
                out.append(line)
                line = word
        if line:
            out.append(line)
    return out


def _checkpoint_lines(payload: dict, workflow_id: str) -> list[tuple[str, tuple[int, int, int]]]:
    """Return [(text, color), …] body lines, header rendered separately."""
    kind = payload.get("kind", "?")
    lines: list[tuple[str, tuple[int, int, int]]] = []

    if kind == "review_plan":
        for ln in _wrap(payload.get("analysis", ""), 100)[:10]:
            lines.append((ln, _FG))
        lines.append(("", _FG))
        for i, step in enumerate(payload.get("plan", []) or [], 1):
            files = ", ".join(step.get("files") or []) or "(none)"
            lines.append((f"{i}. {step.get('title','')}", _ACCENT))
            for ln in _wrap(f"   {step.get('detail','')}", 100)[:3]:
                lines.append((ln, _MUTED))
            lines.append((f"   files: {files}", _MUTED))
    elif kind == "review_code":
        tr = payload.get("test_results") or {}
        passed = tr.get("passed")
        lines.append((f"Tests passed: {passed}", _GREEN if passed else _RED))
        files = [c.get("path", "") for c in payload.get("code_changes", []) or []]
        lines.append(("", _FG))
        lines.append((f"Files touched ({len(files)}):", _ACCENT))
        for p in files[:20]:
            lines.append((f"  {p}", _FG))
    elif kind == "review_commit":
        for ln in _wrap(payload.get("commit_message", "") or "(no message)", 100)[:14]:
            lines.append((ln, _FG))
    else:
        import json as _json
        for ln in _wrap(_json.dumps(payload, indent=2, default=str), 100)[:18]:
            lines.append((ln, _FG))
    return lines


def render_checkpoint_image(payload: dict, workflow_id: str) -> bytes:
    """Render a checkpoint card to PNG bytes. Raises if PIL is not installed."""
    from PIL import Image, ImageDraw

    body = _checkpoint_lines(payload, workflow_id)
    height = IMG_PAD * 2 + TITLE_HEIGHT + LINE_HEIGHT * max(len(body) + 2, 8)
    img = Image.new("RGB", (IMG_WIDTH, height), color=_BG)
    draw = ImageDraw.Draw(img)

    title_font = _font(28)
    sub_font = _font(16)
    body_font = _font(18)

    kind = payload.get("kind", "?").replace("_", " ").title()
    draw.text((IMG_PAD, IMG_PAD), kind, fill=_ACCENT, font=title_font)
    draw.text((IMG_PAD, IMG_PAD + TITLE_HEIGHT), f"workflow {workflow_id}", fill=_MUTED, font=sub_font)

    y = IMG_PAD + TITLE_HEIGHT + LINE_HEIGHT
    for text, colour in body:
        draw.text((IMG_PAD, y), text, fill=colour, font=body_font)
        y += LINE_HEIGHT

    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------- audio ----------


def _narration_text(payload: dict) -> str:
    kind = payload.get("kind", "?")
    if kind == "review_plan":
        plan = payload.get("plan") or []
        steps = ". ".join(s.get("title", "") for s in plan[:5] if s.get("title"))
        return f"Plan ready. {len(plan)} steps. {steps}"
    if kind == "review_code":
        tr = payload.get("test_results") or {}
        passed = "passed" if tr.get("passed") else "failed"
        n = len(payload.get("code_changes") or [])
        return f"Code applied to {n} files. Tests {passed}. Approve to continue."
    if kind == "review_commit":
        return f"Ready to commit. {payload.get('commit_message','')[:200]}"
    return f"Checkpoint of kind {kind}. Decide."


def synthesize_voice(text: str, *, voice: Optional[str] = None) -> Tuple[Optional[bytes], Optional[str], float]:
    """Return (audio_bytes, mime_type, duration_estimate_sec).

    Uses edge-tts (free, no key). Falls back to (None, None, 0.0) if the
    package is missing or the call fails so the caller can post text-only.
    """
    voice = voice or os.getenv("TTS_VOICE", "en-US-AndrewNeural")
    try:
        import edge_tts  # type: ignore
    except ImportError:
        logger.info("edge-tts not installed; skipping voice (pip install edge-tts)")
        return None, None, 0.0

    async def _gen() -> bytes:
        communicate = edge_tts.Communicate(text, voice)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp = f.name
        try:
            await communicate.save(tmp)
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    try:
        try:
            audio = asyncio.run(_gen())
        except RuntimeError:
            # If an event loop is already running, fall back to a thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                audio = ex.submit(lambda: asyncio.run(_gen())).result(timeout=60)
    except Exception as e:  # noqa: BLE001
        logger.warning("edge-tts failed: %s", e)
        return None, None, 0.0

    # rough duration estimate: 150 wpm → ~2.5 wps; mp3 size very variable so
    # just estimate from word count.
    words = len(text.split())
    return audio, "audio/mpeg", max(1.0, words / 2.5)
