#!/usr/bin/env python3
"""Comprehensive test suite for GStack AI Company"""
import sys
import os

# Add project to path
sys.path.insert(0, os.path.dirname(__file__))

print("=" * 60)
print("GStack AI Company - System Test")
print("=" * 60)

# Test 1: Environment loading
print("\n[TEST 1] Environment Variables")
try:
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key:
        print(f"✅ API Key loaded: {api_key[:20]}...")
    else:
        print("❌ No API key found")
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 2: OpenRouter Client
print("\n[TEST 2] OpenRouter Client")
try:
    from agent.openrouter_client import OpenRouterClient
    client = OpenRouterClient()
    response = client.chat([{"role": "user", "content": "Say 'test passed' in 2 words"}], "openrouter/free")
    print(f"✅ Response: {response[:50]}...")
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 3: Orchestrator
print("\n[TEST 3] Orchestrator")
try:
    from orchestration import Orchestrator
    orch = Orchestrator()
    result = orch.execute_workflow("Test workflow")
    print(f"✅ Workflow completed: {result}")
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 4: Context Manager
print("\n[TEST 4] Context Manager")
try:
    from agent.context_manager import ContextManager
    cm = ContextManager()
    cm.add_document("test document", {"source": "test.py"})
    results = cm.get_relevant_context("test")
    print(f"✅ Added document, found {len(results)} results")
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 5: Aider Integration
print("\n[TEST 5] Aider Integration")
try:
    from agent.aider_integration import AiderIntegration
    aider = AiderIntegration()
    print("✅ Aider initialized")
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 6: Task Queue (gstack)
print("\n[TEST 6] Task Queue (gstack)")
try:
    from agent.task_queue import TaskQueue
    queue = TaskQueue()
    queue.push("test_task", {"data": "test"}, priority=1)
    task = queue.pop()
    print(f"✅ Queue push/pop working, got task: {task['type']}")
except Exception as e:
    print(f"❌ Failed: {e}")

# Test 7: Workflow Engine (ruflo)
print("\n[TEST 7] Workflow Engine (ruflo)")
try:
    from agent.workflow_engine import WorkflowEngine, build_ai_development_workflow
    engine = WorkflowEngine()
    wf_def = build_ai_development_workflow()
    engine.define_workflow("dev", wf_def)
    instance_id = engine.start("dev", {"task": "test"})
    print(f"✅ Workflow engine started: {instance_id}")
except Exception as e:
    print(f"❌ Failed: {e}")

print("\n" + "=" * 60)
print("All tests completed!")
print("=" * 60)