"""Quick smoke CLI for the router.

    python -m router.cli "hello" --task analyze
    python -m router.cli "ping" --provider anthropic --model claude-haiku-4-5
"""
from __future__ import annotations

import argparse
import json
import sys

from .router import ModelRouter


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="router.cli")
    p.add_argument("prompt", help="User prompt to send")
    p.add_argument("--task", default="simple", help="Task type (analyze, plan, code, review, simple)")
    p.add_argument("--provider", help="Force a specific provider")
    p.add_argument("--model", help="Override model id (overrides --task)")
    p.add_argument("--max-tokens", type=int, default=256)
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--system", default=None)
    args = p.parse_args(argv)

    router = ModelRouter()
    available = router.available_providers()
    print(f"# Available providers: {available}", file=sys.stderr)

    if args.provider:
        prov = router.providers.get(args.provider)
        if not prov or not prov.is_available():
            print(f"Provider '{args.provider}' is not available.", file=sys.stderr)
            return 2
        model = args.model or prov.default_model()
        messages = []
        if args.system:
            messages.append({"role": "system", "content": args.system})
        messages.append({"role": "user", "content": args.prompt})
        result = prov.chat(messages, model, max_tokens=args.max_tokens, temperature=args.temperature)
        # Still record this call (manual provider path bypasses ModelRouter.chat)
        from storage.accounting import record as record_call

        record_call(
            provider=result.provider,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            task_type=args.task,
        )
    else:
        messages = []
        if args.system:
            messages.append({"role": "system", "content": args.system})
        messages.append({"role": "user", "content": args.prompt})
        result = router.chat(
            messages,
            task_type=args.task,
            model=args.model,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
        )

    print(result.text)
    print(
        json.dumps(
            {
                "provider": result.provider,
                "model": result.model,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
            },
            indent=2,
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
