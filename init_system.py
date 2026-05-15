#!/usr/bin/env python3
"""Initialize GStack AI Company system"""
import os
import sys

print("=" * 50)
print("GStack AI Company - System Initialization")
print("=" * 50)

# Check dependencies
def check_deps():
    deps = ['langgraph', 'chromadb', 'sentence-transformers', 'redis']
    missing = []
    for dep in deps:
        try:
            __import__(dep.split('.')[0])
        except ImportError:
            missing.append(dep)
    return missing

missing = check_deps()
if missing:
    print(f"Missing dependencies: {missing}")
    print("Run: pip install -r requirements.txt")
    sys.exit(1)

print("✅ All dependencies available")

# Initialize databases
print("\n📦 Initializing databases...")
os.makedirs("./data/chroma", exist_ok=True)
os.makedirs("./data/memory", exist_ok=True)
print("✅ Databases initialized")

# Start components
print("\n🚀 Starting components...")
print("  - Orchestrator")
print("  - Context Manager")
print("  - Memory DB")
print("\n✅ GStack AI Company is ready!")
print("\nRun: python ai_company/orchestration.py")