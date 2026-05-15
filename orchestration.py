from typing import Dict, Any, List
import os
import json
from datetime import datetime
import requests
from agent.openrouter_client import OpenRouterClient

# Orchestrator - Central coordinator for AI workers
class Orchestrator:
    def __init__(self):
        self.current_task = None
        self.workflow_history = []
        self.client = OpenRouterClient()
    
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
        """Select appropriate model for step"""
        model_map = {
            "plan": os.getenv("PLANNING_MODEL", "gpt-3.5-turbo"),
            "code": os.getenv("CODE_MODEL", "gpt-4-turbo"),
            "simple": os.getenv("DEFAULT_MODEL", "gpt-3.5-turbo")
        }
        return model_map.get(step_type, "gpt-3.5-turbo")
    
    def execute_step(self, step: Dict) -> Dict[str, Any]:
        """Execute a single workflow step by calling the appropriate model"""
        model = self.route_to_model(step["model"])
        prompt = step["action"]
        
        try:
            # Call OpenRouter API
            response = self.client.chat(
                messages=[{"role": "user", "content": prompt}],
                model=model
            )
            result = {
                "step": step["step"],
                "status": "completed",
                "model_used": model,
                "response": response,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            result = {
                "step": step["step"],
                "status": "failed",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
        
        self.workflow_history.append(result)
        return result
    
    def execute_workflow(self, task: str) -> str:
        """Run full workflow"""
        plan = self.plan_task(task)
        for step in plan["steps"]:
            # Execute each step (would call actual models/tools in real implementation)
            self.execute_step(step)
        return f"Completed workflow for: {task}"
    
    def query_memory(self, query: str) -> List[Dict]:
        """Query previous workflow history"""
        return [h for h in self.workflow_history if query.lower() in str(h).lower()]

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    orch = Orchestrator()
    print(orch.execute_workflow("Add dark mode to app"))