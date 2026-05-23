"""json_repair integration — _extract_json_array now tolerates malformed LLM JSON."""
from __future__ import annotations

import pytest


def test_handles_trailing_comma():
    from orchestrator.nodes import _extract_json_array
    out = _extract_json_array('[{"title": "a", "detail": "d", "files": [],},]')
    assert isinstance(out, list)
    assert out[0]["title"] == "a"


def test_handles_unquoted_keys():
    from orchestrator.nodes import _extract_json_array
    out = _extract_json_array('[{title: "a", detail: "d", files: []}]')
    assert out[0]["title"] == "a"


def test_strips_code_fences():
    from orchestrator.nodes import _extract_json_array
    out = _extract_json_array('```json\n[{"title": "a", "detail": "d", "files": []}]\n```')
    assert out[0]["title"] == "a"


def test_handles_prefix_chatter():
    """LLMs often prepend 'Here is the plan:' or similar."""
    from orchestrator.nodes import _extract_json_array
    out = _extract_json_array(
        'Sure, here is the plan:\n[{"title": "a", "detail": "d", "files": []}]'
    )
    assert out[0]["title"] == "a"


def test_raises_when_no_array():
    from orchestrator.nodes import _extract_json_array
    with pytest.raises(ValueError):
        _extract_json_array("just prose, no brackets at all")


def test_crew_coordinator_extract_also_repairs():
    from orchestrator.crew.coordinator import _extract_json_array
    out = _extract_json_array('[{"title": "x", "files": [],},]')
    assert out[0]["title"] == "x"
