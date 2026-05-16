from typing import Dict, Any, List
import os
import json
import subprocess
from datetime import datetime
import requests
import logging
logger = logging.getLogger(__name__)
from agent.openrouter_client import OpenRouterClient
from agent.task_queue import TaskQueue
from agent.aider_integration import AiderIntegration
from agent.nvidia_client import NvidiaClient

# Orchestrator - Central coordinator for AI workers
class Orchestrator:
    def __init__(self):
        self.current_task = None
        self.workflow_history = []
        self.client = OpenRouterClient()
        # Default model (can be overridden via UI or env)
        self.selected_model = os.getenv("DEFAULT_MODEL", "openrouter/free")
        # Initialize Nvidia client if API key is available
        try:
            self.nvidia = NvidiaClient()
        except Exception as e:
            logger.warning(f"Nvidia client not initialized: {e}")
            self.nvidia = None
        self.repo_path = os.getenv("REPO_PATH", ".")
        # Initialize task queue
        self.task_queue = TaskQueue()
        # Initialize Aider for code editing
        self.aider = AiderIntegration(repo_path=self.repo_path)
        # Token accounting and memory persistence
        from agent.memory_persistence import TokenAccounting, MemoryPersistence
        self.accounting = TokenAccounting()
        self.memory = MemoryPersistence()
        # Activity logger – writes simple messages to ./data/activity.log
        self.activity_log_path = os.path.join(self.memory.persist_dir, "activity.log")
        # Ensure the file exists
        open(self.activity_log_path, "a").close()
    def log_activity(self, message: str):
        """Append a human‑readable activity line to the log file."""
        try:
            with open(self.activity_log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} - {message}\n")
        except Exception as e:
            # Non‑critical – keep silent
            pass
        # expose method to change model
        self.set_model = lambda model_id: setattr(self, 'selected_model', model_id)
    
    def plan_task(self, task_description: str) -> List[Dict[str, Any]]:
        """Break down task into actionable steps"""
        steps = [
            {
                "step": "analyze",
                "action": f"Analyze requirements for: {task_description}",
                "model": "simple"
            },
            {
                "step": "plan",
                "action": "Create implementation plan",
                "model": "plan"
            },
            {
                "step": "code_generation",
                "action": "Generate code based on plan",
                "model": "code"
            },
            {
                "step": "test",
                "action": "Run tests",
                "model": "simple"
            },
            {
                "step": "commit",
                "action": "Commit changes",
                "model": "simple"
            }
        ]
        return steps
    
    def route_to_model(self, step_type: str) -> str:
        """Return model based on selected model or step type.
        
        - ``step_type`` comes from the ``model`` field of a step (e.g. "simple", "plan", "code").
        - For code generation we prefer the Nvidia NIM model if the client is available.
        - All other steps fall back to the globally selected model.
        """
        if step_type == "code" and self.nvidia:
            # Use a representative Nvidia NIM model
            return "nvidia-nim/deepseek-v4-flash"
        # Default – use the globally selected model (openrouter/free or any UI choice)
        return self.selected_model
    
    def safe_api_call(self, prompt: str, model: str = None) -> Dict[str, Any]:
        """Make API call with error handling"""
        model = model or self.route_to_model("simple")
        try:
            response = self.client.chat(
                messages=[{"role": "user", "content": prompt}],
                model=model
            )
            return {"status": "success", "response": response}
        except requests.exceptions.Timeout:
            return {"status": "error", "error": "API timeout"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def execute_test_step(self, step: Dict) -> Dict[str, Any]:
        """Execute testing step"""
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "-v"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            return {
                "step": step["step"],
                "status": "completed" if result.returncode == 0 else "failed",
                "output": result.stdout[-500:],
                "timestamp": datetime.now().isoformat()
            }
        except subprocess.TimeoutExpired:
            return {
                "step": step["step"],
                "status": "timeout",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "step": step["step"],
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def execute_commit_step(self, step: Dict) -> Dict[str, Any]:
        """Execute git commit step"""
        try:
            subprocess.run(["git", "add", "."], cwd=self.repo_path, check=True)
            subprocess.run(
                ["git", "commit", "-m", "AI-generated changes"],
                cwd=self.repo_path,
                check=True,
                capture_output=True
            )
            return {
                "step": step["step"],
                "status": "completed",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "step": step["step"],
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def execute_code_step(self, step: Dict) -> Dict[str, Any]:
        """Execute code generation step using Aider"""
        try:
            # Generate code using OpenRouter, then apply via Aider
            # First, get a plan from previous step or generate
            prompt = step["action"]
            # Use API to get more detailed implementation suggestion
            api_result = self.safe_api_call(prompt, self.route_to_model("code"))
            code_suggestion = api_result.get("response", "") if api_result.get("status") == "success" else prompt
            
            # Apply the code changes using Aider
            success = self.aider.apply_prompt(code_suggestion)
            return {
                "step": step["step"],
                "status": "completed" if success else "failed",
                "timestamp": datetime.now().isoformat(),
                "files_changed": self.aider.get_file_status() if success else [],
                "code_suggestion": code_suggestion[:200] if success else None
            }
        except Exception as e:
            return {
                "step": step["step"],
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def execute_step(self, step: Dict) -> Dict[str, Any]:
        """Execute a single workflow step"""
        if step["step"] == "test":
            return self.execute_test_step(step)
        elif step["step"] == "commit":
            return self.execute_commit_step(step)
        elif step["step"] == "code_generation":
            return self.execute_code_step(step)
        else:
            # Default: API call for analyze/plan
            model = self.route_to_model(step["model"])
            prompt = step["action"]
            result = self.safe_api_call(prompt, model)
            
            return {
                "step": step["step"],
                "status": result.get("status", "unknown"),
                "response": result.get("response", ""),
                "error": result.get("error", ""),
                "timestamp": datetime.now().isoformat()
            }
    
    def execute_workflow(self, task: str) -> str:
        """Run full workflow"""
        plan = self.plan_task(task)
        for step in plan:
            result = self.execute_step(step)
            self.workflow_history.append(result)
            # Persist each step result to memory for later queries
            try:
                import json as _json
                self.memory.add_document(_json.dumps(result), {"type": "workflow_step", "task": task, "step": step.get("step")})
            except Exception:
                pass
        return f"Completed workflow for: {task}"
    
    def query_memory(self, query: str) -> List[Dict]:
        """Query previous workflow history"""
        return [h for h in self.workflow_history if query.lower() in str(h).lower()]
    
    def get_queue_status(self) -> Dict:
        """Get current task queue status"""
        return {
            "queue_size": self.task_queue.size(),
            "queue_empty": self.task_queue.size() == 0
        }

if __name__ == "__main__":
    orch = Orchestrator()
    print(orch.execute_workflow("Add dark mode to app"))