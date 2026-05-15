"""
Task Queue using gstack (garrytan/gstack).

gstack provides a simple stack-based queue for managing tasks.
We use it to queue AI work items with priorities.
"""
import os
import json
import logging
from typing import Dict, Any, Optional, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to import gstack; fall back to local implementation if unavailable
try:
    import gstack
    HAS_GSTACK = True
except ImportError:
    HAS_GSTACK = False
    logger.warning("gstack not installed, using fallback queue")


class TaskQueue:
    """
    High-level task queue wrapper around gstack.
    If gstack is unavailable, uses an in-memory fallback.
    """

    def __init__(self, queue_name: str = "ai-company-tasks"):
        """Initialize the task queue.

        Args:
            queue_name: Name of the queue (used as key in gstack).
        """
        self.queue_name = queue_name
        self._fallback_queue: List[Dict[str, Any]] = []

        if HAS_GSTACK:
            try:
                self.client = gstack.Client()
                logger.info(f"gstack client initialized for queue: {queue_name}")
            except Exception as e:
                logger.warning(f"gstack client init failed ({e}), using fallback")
                self.client = None
        else:
            self.client = None

    def push(self, task_type: str, payload: Dict[str, Any], priority: int = 0) -> bool:
        """Push a task onto the queue.

        Args:
            task_type: Type of task (e.g., "code", "test", "analyze").
            payload: Task data (file path, prompt, etc.).
            priority: Priority level (higher = processed first).

        Returns:
            True if pushed successfully.
        """
        task = {
            "id": self._generate_id(),
            "type": task_type,
            "payload": payload,
            "priority": priority,
            "status": "queued",
            "created_at": _iso_now()
        }

        if self.client:
            try:
                self.client.push(self.queue_name, json.dumps(task))
                logger.info(f"Pushed task {task['id']} to gstack queue")
                return True
            except Exception as e:
                logger.error(f"gstack push failed ({e}), falling back")

        # Fallback: in-memory queue
        self._fallback_queue.append(task)
        return True

    def pop(self) -> Optional[Dict[str, Any]]:
        """Pop the next task from the queue.

        Returns:
            The next task dict, or None if queue is empty.
        """
        if self.client:
            try:
                raw = self.client.pop(self.queue_name)
                if raw:
                    task = json.loads(raw)
                    task["status"] = "processing"
                    return task
            except Exception as e:
                logger.error(f"gstack pop failed ({e}), falling back")

        # Fallback
        if self._fallback_queue:
            task = self._fallback_queue.pop(0)
            task["status"] = "processing"
            return task

        return None

    def peek(self) -> Optional[Dict[str, Any]]:
        """Peek at the next task without removing it.

        Returns:
            The next task dict, or None if empty.
        """
        if self.client:
            try:
                raw = self.client.peek(self.queue_name)
                if raw:
                    return json.loads(raw)
            except Exception as e:
                logger.error(f"gstack peek failed ({e})")

        if self._fallback_queue:
            return self._fallback_queue[0]
        return None

    def size(self) -> int:
        """Return number of tasks in queue."""
        if self.client:
            try:
                return self.client.size(self.queue_name)
            except Exception:
                pass
        return len(self._fallback_queue)

    def clear(self) -> bool:
        """Remove all tasks from the queue."""
        if self.client:
            try:
                self.client.clear(self.queue_name)
                return True
            except Exception as e:
                logger.error(f"gstack clear failed ({e})")

        self._fallback_queue.clear()
        return True

    def _generate_id(self) -> str:
        """Generate a unique task ID."""
        import uuid
        return str(uuid.uuid4())[:8]


def _iso_now() -> str:
    """Return current time in ISO format."""
    from datetime import datetime
    return datetime.now().isoformat()