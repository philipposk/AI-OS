"""Phase W: real multi-agent crew.

Replaces nodes.plan_node's single-LLM-role-switch with multiple specialist
agents (Planner / Coder / Tester / Reviewer / Critic) that post to a shared
bus and a coordinator that reconciles their output.

Activated by `CREW_MODE=true` env or `state["crew_mode"] = True` from the
caller. When off, plan_node behaves exactly as before — the crew code is
inert.

Per-role model overrides:
    CREW_PLANNER_MODEL=claude-opus-4-7
    CREW_CRITIC_MODEL=claude-sonnet-4-6
    CREW_CODER_MODEL=...
    CREW_TESTER_MODEL=...
    CREW_REVIEWER_MODEL=...
"""
from .bus import Message, MessageBus, get_bus
from .coordinator import coordinate_plan, is_crew_mode
from .roles import Coder, Critic, Planner, Reviewer, Role, Tester

__all__ = [
    "Message", "MessageBus", "get_bus",
    "Role", "Planner", "Coder", "Tester", "Reviewer", "Critic",
    "coordinate_plan", "is_crew_mode",
]
