"""
Workflow Engine using ruflo (ruvnet/ruflo).

ruflo provides a declarative state-machine workflow system.
We use it to define multi-step AI pipelines with proper state tracking.
"""
import os
import json
import logging
from typing import Dict, Any, List, Callable, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to import ruflo; fall back to local implementation if unavailable
try:
    import ruflo
    HAS_RUFLO = True
except ImportError:
    HAS_RUFLO = False
    logger.warning("ruflo not installed, using fallback workflow engine")


class WorkflowEngine:
    """
    Declarative workflow engine wrapping ruflo.
    
    Define workflows as state machines with transitions:
    {
        "name": "code_review",
        "states": ["analyze", "plan", "code", "test", "commit"],
        "transitions": [
            {"from": "analyze", "to": "plan", "condition": "analysis_complete"},
            ...
        ]
    }
    """

    def __init__(self):
        """Initialize the workflow engine."""
        self.workflows: Dict[str, Dict] = {}
        self.instances: Dict[str, Dict] = {}
        self._state_handlers: Dict[str, Callable] = {}

        if HAS_RUFLO:
            try:
                self.engine = ruflo.Engine()
                logger.info("ruflo engine initialized")
            except Exception as e:
                logger.warning(f"ruflo engine init failed ({e}), using fallback")
                self.engine = None
        else:
            self.engine = None

    def register_state_handler(self, state_name: str, handler: Callable) -> None:
        """Register a function to execute when entering a state.

        Args:
            state_name: Name of the state.
            handler: Callable that receives the workflow context dict.
        """
        self._state_handlers[state_name] = handler
        logger.info(f"Registered handler for state: {state_name}")

    def define_workflow(self, name: str, definition: Dict[str, Any]) -> bool:
        """Define a new workflow.

        Args:
            name: Unique workflow name/ID.
            definition: Workflow spec with 'states' and 'transitions'.

        Returns:
            True if workflow was defined successfully.
        """
        try:
            self.workflows[name] = definition

            if self.engine:
                try:
                    # Create ruflo state machine
                    machine = ruflo.StateMachine(name=name)
                    for state in definition.get("states", []):
                        machine.add_state(state)
                    for trans in definition.get("transitions", []):
                        machine.add_transition(
                            trans["from"], trans["to"],
                            condition=trans.get("condition")
                        )
                    self.engine.register(machine)
                except Exception as e:
                    logger.warning(f"ruflo registration failed ({e}), using fallback")

            return True
        except Exception as e:
            logger.error(f"Failed to define workflow '{name}': {e}")
            return False

    def start(self, workflow_name: str, context: Dict[str, Any] = None) -> Optional[str]:
        """Start a new workflow instance.

        Args:
            workflow_name: Name of the workflow to start.
            context: Initial context data for the workflow.

        Returns:
            Instance ID, or None on failure.
        """
        import uuid
        instance_id = str(uuid.uuid4())[:12]

        instance = {
            "id": instance_id,
            "workflow": workflow_name,
            "context": context or {},
            "current_state": None,
            "history": [],
            "status": "running",
            "started_at": _iso_now()
        }

        self.instances[instance_id] = instance
        logger.info(f"Started workflow instance {instance_id} ({workflow_name})")

        # Start with first state
        wf = self.workflows.get(workflow_name, {})
        states = wf.get("states", [])
        if states:
            self._transition(instance_id, states[0])

        return instance_id

    def step(self, instance_id: str, result: Any = None) -> Dict[str, Any]:
        """Advance workflow to next state based on result.

        Args:
            instance_id: The workflow instance ID.
            result: Output from the current state's handler.

        Returns:
            Updated instance info dict.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            raise ValueError(f"Instance {instance_id} not found")

        wf = self.workflows.get(instance["workflow"], {})
        transitions = wf.get("transitions", [])
        current = instance["current_state"]

        # Store result in context
        if current and result is not None:
            instance["context"][f"{current}_result"] = result

        # Find next transition
        next_state = None
        for trans in transitions:
            if trans["from"] == current:
                condition = trans.get("condition")
                if condition is None or self._check_condition(condition, instance["context"]):
                    next_state = trans["to"]
                    break

        if next_state:
            self._transition(instance_id, next_state)
        else:
            # No more transitions, complete
            instance["status"] = "completed"
            instance["completed_at"] = _iso_now()
            logger.info(f"Workflow instance {instance_id} completed")

        return instance

    def get_instance(self, instance_id: str) -> Dict[str, Any]:
        """Get current state of a workflow instance."""
        return self.instances.get(instance_id, {})

    def _transition(self, instance_id: str, to_state: str) -> None:
        """Execute a state transition.

        Args:
            instance_id: The workflow instance ID.
            to_state: The state to transition into.
        """
        instance = self.instances[instance_id]
        from_state = instance["current_state"]

        instance["history"].append({
            "from": from_state,
            "to": to_state,
            "timestamp": _iso_now()
        })
        instance["current_state"] = to_state

        # Call registered handler for the new state
        handler = self._state_handlers.get(to_state)
        if handler:
            try:
                handler(instance["context"])
            except Exception as e:
                logger.error(f"Handler for state '{to_state}' failed: {e}")
                instance["status"] = "error"
                instance["error"] = str(e)
        else:
            logger.debug(f"No handler registered for state '{to_state}'")

    def _check_condition(self, condition: str, context: Dict) -> bool:
        """Evaluate a transition condition against context.

        Simple condition evaluator — supports checks like:
          "analysis_complete", "has_code_changes", "tests_passed"
        """
        key = condition.replace("_complete", "_result").replace("_passed", "_result")
        result = context.get(key)
        return bool(result)


# --- Built-in workflow definitions ---

def build_ai_development_workflow() -> Dict[str, Any]:
    """Standard AI-assisted development workflow definition."""
    return {
        "states": [
            "requirement_analysis",
            "implementation_planning",
            "code_generation",
            "code_review",
            "test_execution",
            "commit_and_push",
            "done"
        ],
        "transitions": [
            {"from": "requirement_analysis", "to": "implementation_planning"},
            {"from": "implementation_planning", "to": "code_generation"},
            {"from": "code_generation", "to": "code_review"},
            {"from": "code_review", "to": "test_execution"},
            {"from": "test_execution", "to": "commit_and_push"},
            {"from": "commit_and_push", "to": "done"},
        ]
    }


def build_research_workflow() -> Dict[str, Any]:
    """Research and analysis workflow definition."""
    return {
        "states": [
            "query_understanding",
            "research",
            "synthesis",
            "report_generation",
            "done"
        ],
        "transitions": [
            {"from": "query_understanding", "to": "research"},
            {"from": "research", "to": "synthesis"},
            {"from": "synthesis", "to": "report_generation"},
            {"from": "report_generation", "to": "done"},
        ]
    }


def _iso_now() -> str:
    from datetime import datetime
    return datetime.now().isoformat()