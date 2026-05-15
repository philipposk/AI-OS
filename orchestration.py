from typing import Dict, Any, List
import os
import json
import subprocess
from datetime import datetime
import requests
from agent.openrouter_client import OpenRouterClient
from agent.task_queue import TaskQueue
from agent.workflow_engine import WorkflowEngine, build_ai_development_workflow

# Orchestrator - Central coordinator for AI workers
class Orchestrator:
    def __init__(self):
        self.current_task = None
        self.workflow_history = []
        self.client = OpenRouterClient()
        self.repo_path = os.getenv("REPO_PATH", ".")
        # Initialize task queue
        self.task_queue = TaskQueue()
        # Initialize workflow engine
        self.workflow_engine = WorkflowEngine()
        self._register_workflow_handlers()
    
    def _register_workflow_handlers(self):
        """Register handlers for workflow states"""
        self.workflow_engine.register_state_handler("requirement_analysis", self._handle_requirement_analysis)
        self.workflow_engine.register_state_handler("implementation_planning", self._handle_implementation_planning)
        self.workflow_engine.register_state_handler("code_generation", self._handle_code_generation)
        self.workflow_engine.register_state_handler("code_review", self._handle_code_review)
        self.workflow_engine.register_state_handler("test_execution", self._handle_test_execution)
        self.workflow_engine.register_state_handler("commit_and_push", self._handle_commit_and_push)
    
    def plan_task(self, task_description: str) -> Dict[str, Any]:
        """Break down task into actionable steps"""
        steps = [
            {"step": "analyze", "action": f"Analyze requirements for: {task_description}", "model": "simple"},
            {"step": "plan", "action": "Create implementation plan", "model": "plan"},
            {"step": "execute", "action": "Execute implementation", "model": "code"},
            {"step": "test", "action": "Run tests", "model": "simple"},
            {"step": "commit", "action": "Commit changes", "model": "simple"}
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
            return {"step": step["step"], "status": "timeout", "timestamp": datetime.now().isoformat()}
        except Exception as e:
            return {"step": step["step"], "status": "failed", "error": str(e), "timestamp": datetime.now().isoformat()}
    
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
            return {"step": step["step"], "status": "completed", "timestamp": datetime.now().isoformat()}
        except Exception as e:
            return {"step": step["step"], "status": "failed", "error": str(e), "timestamp": datetime.now().isoformat()}
    
    def _handle_requirement_analysis(self, context: Dict):
        """Handle requirement analysis state"""
        task = context.get("task", "Unknown")
        response = self.safe_api_call(f"Analyze requirements for: {task}")
        context["requirement_analysis_result"] = response.get("response", "")
    
    def _handle_implementation_planning(self, context: Dict):
        """Handle implementation planning state"""
        analysis = context.get("requirement_analysis_result", "")
        response = self.safe_api_call(f"Create implementation plan for: {analysis}")
        context["implementation_plan_result"] = response.get("response", "")
    
    def _handle_code_generation(self, context: Dict):
        """Handle code generation state"""
        plan = context.get("implementation_plan_result", "")
        response = self.safe_api_call(f"Generate code for: {plan}")
        context["code_generation_result"] = response.get("response", "")
    
    def _handle_code_review(self, context: Dict):
        """Handle code review state"""
        code = context.get("code_generation_result", "")
        response = self.safe_api_call(f"Review code: {code}")
        context["code_review_result"] = response.get("response", "")
    
    def _handle_test_execution(self, context: Dict):
        """Handle test execution state"""
        result = self.execute_test_step({"step": "test", "action": "Run tests"})
        context["test_execution_result"] = result
    
    def _handle_commit_and_push(self, context: Dict):
        """Handle commit and push state"""
        result = self.execute_commit_step({"step": "commit", "action": "Commit changes"})
        context["commit_result"] = result
    
    def execute_step(self, step: Dict) -> Dict[str, Any]:
        """Execute a single workflow step"""
        if step["step"] == "test":
            return self.execute_test_step(step)
        elif step["step"] == "commit":
            return self.execute_commit_step(step)
        elif step["step"] == "execute":
            return self._handle_code_generation({})
        
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
        """Run full workflow using workflow engine"""
        # Start workflow engine
        wf_def = build_ai_development_workflow()
        self.workflow_engine.define_workflow("dev", wf_def)
        instance_id = self.workflow_engine.start("dev", {"task": task})
        
        # Run through steps
        plan = self.plan_task(task)
        for step in plan["steps"]:
            self.execute_step(step)
        
        return f"Completed workflow for: {task} (Instance: {instance_id})"
    
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