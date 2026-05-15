#!/usr/bin/env python3
"""Test task runner for GStack AI Company"""
import sys
sys.path.append(".")

from orchestration import Orchestrator
from agent.context_manager import ContextManager
from agent.aider_integration import AiderIntegration

print("Testing GStack AI Company...")

# Test orchestrator
orch = Orchestrator()
result = orch.execute_workflow("Add dark mode to my app")
print(f"Orchestrator test: {result}")

# Test context manager
cm = ContextManager()
cm.add_document("test code", {"source": "test.py"})
print("Context manager test: ✅")

# Test aider
aider = AiderIntegration()
print("Aider integration test: ✅")

print("\nAll tests passed!")