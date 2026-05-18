"""Checkpoint-card image render + TTS bytes. No network."""
from __future__ import annotations

import io

import pytest


def test_render_checkpoint_image_review_plan_returns_png():
    from communication import media as m

    payload = {
        "kind": "review_plan",
        "analysis": "User wants a --version flag on cli.py. Low risk.",
        "plan": [
            {"title": "Add --version to argparse", "detail": "wire it", "files": ["cli.py"]},
            {"title": "Update tests", "detail": "add cli test", "files": ["tests/test_cli.py"]},
        ],
    }
    png = m.render_checkpoint_image(payload, "wf-abc")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # Basic sanity: PIL can re-open it
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    assert img.size[0] == m.IMG_WIDTH
    assert img.size[1] >= 200


def test_render_checkpoint_image_review_code():
    from communication import media as m
    png = m.render_checkpoint_image({
        "kind": "review_code",
        "test_results": {"passed": True},
        "code_changes": [{"path": "cli.py"}, {"path": "x.py"}],
    }, "wf-xyz")
    assert png.startswith(b"\x89PNG")


def test_render_checkpoint_image_unknown_kind_renders_json_dump():
    from communication import media as m
    png = m.render_checkpoint_image({"kind": "something_new", "a": 1}, "wf-1")
    assert png.startswith(b"\x89PNG")


def test_narration_text_per_kind():
    from communication import media as m
    plan = m._narration_text({"kind": "review_plan", "plan": [
        {"title": "First step"}, {"title": "Second step"},
    ]})
    assert "2 steps" in plan and "First step" in plan
    code = m._narration_text({"kind": "review_code", "test_results": {"passed": True}, "code_changes": [{"path": "a"}, {"path": "b"}]})
    assert "Code applied to 2 files" in code and "passed" in code
    fail = m._narration_text({"kind": "review_code", "test_results": {"passed": False}, "code_changes": []})
    assert "failed" in fail
    commit = m._narration_text({"kind": "review_commit", "commit_message": "Add flag"})
    assert "commit" in commit.lower()


def test_synthesize_voice_returns_none_when_edge_tts_missing(monkeypatch):
    """Force ImportError on edge_tts → graceful (None, None, 0.0)."""
    from communication import media as m
    import sys
    monkeypatch.setitem(sys.modules, "edge_tts", None)
    audio, mime, dur = m.synthesize_voice("hello world")
    assert audio is None and mime is None and dur == 0.0


def test_synthesize_voice_uses_edge_tts_when_present(monkeypatch):
    from communication import media as m

    class _FakeCommunicate:
        def __init__(self, text, voice): self.text = text; self.voice = voice
        async def save(self, path):
            with open(path, "wb") as f:
                f.write(b"FAKE_MP3_PAYLOAD")

    import sys, types
    fake_module = types.SimpleNamespace(Communicate=_FakeCommunicate)
    monkeypatch.setitem(sys.modules, "edge_tts", fake_module)

    audio, mime, dur = m.synthesize_voice("one two three", voice="en-US-AndrewNeural")
    assert audio == b"FAKE_MP3_PAYLOAD"
    assert mime == "audio/mpeg"
    assert dur > 0.0


def test_synthesize_voice_swallows_runtime_error(monkeypatch):
    from communication import media as m

    class _FakeCommunicate:
        def __init__(self, *a, **k): pass
        async def save(self, path): raise RuntimeError("network down")

    import sys, types
    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=_FakeCommunicate))
    audio, mime, dur = m.synthesize_voice("hi")
    assert audio is None and mime is None and dur == 0.0
