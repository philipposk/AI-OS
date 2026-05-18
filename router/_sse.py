"""Minimal Server-Sent Events parser for OpenAI-compatible streaming endpoints.

Spec: lines `data: <json>\n`, terminator `data: [DONE]`. Each chunk JSON has
choices[0].delta.content for the next text fragment, and an optional usage
block on the final chunk.
"""
from __future__ import annotations

import json
from typing import Iterator, Tuple


def parse_openai_sse(resp) -> Iterator[Tuple[str, dict]]:
    """Yield (text_fragment, raw_chunk_dict) tuples. text_fragment may be ''
    on usage-only chunks. Caller iterates until the stream ends.
    """
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        data = raw[5:].strip()
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices") or []
        text = ""
        if choices:
            text = (choices[0].get("delta") or {}).get("content") or ""
        yield text, chunk
