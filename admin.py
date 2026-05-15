#!/usr/bin/env python3
"""CLI admin console for GStack AI Company"""
import argparse
import json
from orchestration import Orchestrator

def main():
    parser = argparse.ArgumentParser(description='GStack AI Company Admin')
    parser.add_argument('task', help='Task to execute')
    parser.add_argument('--status', action='store_true', help='Show queue status')
    parser.add_argument('--memory', help='Search memory')
    parser.add_argument('--history', action='store_true', help='Show workflow history')

    args = parser.parse_args()

    orch = Orchestrator()

    if args.status:
        status = orch.get_queue_status()
        print(json.dumps(status, indent=2))
        return

    if args.memory:
        results = orch.query_memory(args.memory)
        for r in results:
            print(json.dumps(r, indent=2))
        return

    if args.history:
        result = orch.execute_workflow(args.task)
        print("Workflow result:", result)
        print("\nWorkflow history:")
        for h in orch.workflow_history:
            print(f"  - {h.get('step', 'unknown')}: {h.get('status', 'unknown')}")
        return

    # Default: execute task
    result = orch.execute_workflow(args.task)
    print(result)

if __name__ == "__main__":
    main()