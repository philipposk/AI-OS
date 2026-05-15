from typing import Dict, Any, List
import os
import json
import subprocess
from datetime import datetime
import requests
from agent.openrouter_client import OpenRouterClient

# Orchestrator - Central coordinator for AI workers
class Orchestrator:
    def __init__(self):
        self.current_task = None
        self.workflow_history = []
        self.client = OpenRouterClient()
        self.repo_path = os.getenv("REPO_PATH", ".")
    
    def plan_task(self, task_description: str) -> Dict[str, Any]:
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
                "step": "execute",
                "action": "Execute implementation",
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
        return {"steps": steps, "original_task": task_description}
    
    def route_to_model(self, step_type: str) -> str:
        """Always use OpenRouter/free model"""
        return "openrouter/free"
    
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
        except subprocess.CalledProcessError as e:
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
        elif step["step"] == "execute":
            return self.execute_code_step(step)
        elif step["step"] == "commit":
            return self.execute_commit_step(step)
        
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
    
    def execute_code_step(self, step: Dict) -> Dict[str, Any]:
        """Execute code implementation step using Aider"""
        try:
            aider = AiderIntegration(self.repo_path)
            success = aider.apply_prompt(step["action"])
            return {
                "step": step["step"],
                "status": "completed" if success else "failed",
                "timestamp": datetime.now().isoformat(),
                "files_changed": aider.get_file_status() if success else []
            }
        except Exception as e:
            return {
                "step": step["step"],
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def execute_workflow(self, task: str) -> str:
        """Run full workflow"""
        plan = self.plan_task(task)
        for step in plan["steps"]:
            result = self.execute_step(step)
            self.workflow_history.append(result)
        return f"Completed workflow for: {task}"
    
    def query_memory(self, query: str) -> List[Dict]:
        """Query previous workflow history"""
        return [h for h in self.workflow_history if query.lower() in str(h).lower()]

if __name__ == "__main__":
    orch = Orchestrator()
    print(orch.execute_workflow("Add dark mode to app"))