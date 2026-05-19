"""Vision content-block helpers.

OpenAI shape is the lingua franca: `content` is a list of blocks, each
either `{"type":"text","text":...}` or
`{"type":"image_url","image_url":{"url": "<data: or https:>"}}`.

Anthropic uses a different shape — `{"type":"image","source":{"type":"base64",
"media_type":"image/png","data":"<b64>"}}` for inline images and
`{"type":"image","source":{"type":"url","url":"https://..."}}` for URLs.

Helpers:
  has_images(messages)          → True if any content block is an image
  text_only(messages)           → flatten to plain string (drops images);
                                   what we send to Groq/NVIDIA/Ollama.
  to_anthropic(messages)        → translate blocks to Anthropic shape.
  parse_data_url(url)           → (media_type, b64_data) or (None, None)
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Tuple

VISION_CAPABLE = frozenset({"anthropic", "openrouter"})

_DATA_URL_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", re.DOTALL)


def parse_data_url(url: str) -> Tuple[str | None, str | None]:
    """`data:image/png;base64,<b64>` → ("image/png", "<b64>"). Else (None, None)."""
    if not isinstance(url, str):
        return None, None
    m = _DATA_URL_RE.match(url)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _iter_blocks(content: Any) -> Iterable[dict]:
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                yield b


def has_images(messages: List[dict]) -> bool:
    for m in messages:
        for b in _iter_blocks(m.get("content")):
            if b.get("type") == "image_url" or b.get("type") == "image":
                return True
    return False


def text_only(messages: List[dict]) -> List[dict]:
    """Strip image blocks so text-only providers don't error.

    Each message's `content` is collapsed to a single string built from
    its text blocks (image blocks replaced by a `[image omitted]` marker).
    """
    out: List[dict] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        parts: List[str] = []
        for b in _iter_blocks(content):
            t = b.get("type")
            if t == "text":
                parts.append(str(b.get("text") or ""))
            elif t in ("image_url", "image"):
                parts.append("[image omitted — text-only provider]")
        out.append({"role": m["role"], "content": "\n".join(p for p in parts if p)})
    return out


def to_anthropic(messages: List[dict]) -> List[dict]:
    """Translate OpenAI-shape blocks → Anthropic blocks.

    Anthropic's `messages[*].content` accepts a list of blocks with
    `{type:'text', text}` and `{type:'image', source}`. `source` is
    either `{type:'base64', media_type, data}` or `{type:'url', url}`.

    Plain-string contents are passed through unchanged (Anthropic accepts
    a string there too).
    """
    out: List[dict] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        new_blocks: List[dict] = []
        for b in _iter_blocks(content):
            t = b.get("type")
            if t == "text":
                new_blocks.append({"type": "text", "text": str(b.get("text") or "")})
            elif t == "image_url":
                url = (b.get("image_url") or {}).get("url") if isinstance(b.get("image_url"), dict) else b.get("image_url")
                if not isinstance(url, str):
                    continue
                media_type, data = parse_data_url(url)
                if data is not None:
                    new_blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type or "image/png", "data": data},
                    })
                else:
                    new_blocks.append({
                        "type": "image",
                        "source": {"type": "url", "url": url},
                    })
            elif t == "image":
                # Already Anthropic shape — pass through.
                new_blocks.append(b)
        out.append({"role": m["role"], "content": new_blocks})
    return out
