"""Obsidian vault mirror tests. No real Obsidian; just a tmp directory."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "obs.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")


def test_vault_root_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    from storage import obsidian as obs
    assert obs.vault_root() is None


def test_vault_root_returns_none_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("OBSIDIAN_DISABLED", "1")
    from storage import obsidian as obs
    assert obs.vault_root() is None


def test_vault_root_returns_none_for_nonexistent_path(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "no_such_dir"))
    monkeypatch.delenv("OBSIDIAN_DISABLED", raising=False)
    from storage import obsidian as obs
    assert obs.vault_root() is None


def test_mirror_note_writes_markdown_with_frontmatter(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    monkeypatch.delenv("OBSIDIAN_DISABLED", raising=False)
    from storage import obsidian as obs

    res = obs.mirror_note(
        doc_id=42,
        kind="review",
        text="Added --version flag to cli.py.\nTests passed.",
        workflow_id="wf-xyz",
        meta={"task": "Add --version flag", "files": ["cli.py"]},
        created_at="2026-05-18T07:00:00Z",
    )
    assert res is not None
    assert res.path.exists()
    body = res.path.read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert "id: 42" in body
    assert "kind: review" in body
    assert "workflow_id: wf-xyz" in body
    assert "Added --version flag" in body
    # Bucketed under workflow_id
    assert "ai_company/wf-xyz" in str(res.path).replace("\\", "/")


def test_mirror_returns_none_when_vault_disabled(monkeypatch):
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    from storage import obsidian as obs
    res = obs.mirror_note(doc_id=1, kind="note", text="hi")
    assert res is None


def test_search_vault_finds_substring(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    monkeypatch.delenv("OBSIDIAN_DISABLED", raising=False)
    (tmp_path / "alpha.md").write_text("Auth middleware uses HS256 signing.", encoding="utf-8")
    (tmp_path / "beta.md").write_text("Beta meta information.", encoding="utf-8")
    (tmp_path / "gamma.md").write_text("Auth handler scope: tokens. Auth!", encoding="utf-8")
    from storage import obsidian as obs

    hits = obs.search_vault("auth")
    assert hits and hits[0]["path"].endswith("gamma.md")  # gamma has 2 'auth' matches → higher score
    assert any(h["path"].endswith("alpha.md") for h in hits)
    assert not any(h["path"].endswith("beta.md") for h in hits)


def test_search_vault_empty_query_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    monkeypatch.delenv("OBSIDIAN_DISABLED", raising=False)
    from storage import obsidian as obs
    assert obs.search_vault("") == []


def test_search_vault_skips_huge_files(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    monkeypatch.delenv("OBSIDIAN_DISABLED", raising=False)
    huge = tmp_path / "huge.md"
    huge.write_text("auth\n" * 50000, encoding="utf-8")  # ~250 KB
    small = tmp_path / "small.md"
    small.write_text("auth here", encoding="utf-8")

    from storage import obsidian as obs
    hits = obs.search_vault("auth", max_bytes_per_file=10_000)
    paths = [h["path"] for h in hits]
    assert "small.md" in paths
    assert "huge.md" not in paths


def test_memory_add_mirrors_to_vault(monkeypatch, tmp_path):
    """End-to-end: memory.add() with vault env set writes both SQLite + vault file."""
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    monkeypatch.delenv("OBSIDIAN_DISABLED", raising=False)
    from storage import memory as mem

    doc_id = mem.add("Switched router to LangGraph", kind="review", workflow_id="wf-1",
                     meta={"files": ["orchestrator/graph.py"]})
    assert doc_id > 0
    files = list((tmp_path / "ai_company" / "wf-1").rglob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Switched router to LangGraph" in text
    assert f"id: {doc_id}" in text


def test_bulk_export_writes_all_existing_rows(monkeypatch, tmp_path):
    from storage import memory as mem
    from storage import obsidian as obs

    # First, write rows WITHOUT vault configured.
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    mem.add("alpha note", kind="note")
    mem.add("beta note", kind="review", workflow_id="wf-9")
    # Now enable vault + export
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    n = obs.bulk_export()
    assert n == 2
    md_files = list(tmp_path.rglob("*.md"))
    assert len(md_files) == 2


def test_yaml_dump_handles_lists_and_quoted_strings():
    from storage.obsidian import _yaml_dump
    out = _yaml_dump({"a": 1, "b": "has: colons", "tags": ["x", "y"], "c": None})
    assert "a: 1" in out
    # colons trigger JSON-style quoting
    assert "\"has: colons\"" in out
    # tags rendered as list
    assert "tags:" in out and "  - x" in out and "  - y" in out
    # None values dropped
    assert "c:" not in out
