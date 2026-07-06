from __future__ import annotations

import argparse
import os
import sys

from .loop import AgentConfig, run
from .providers.anthropic_provider import AnthropicProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-agent",
        description="Vision-LLM computer-control agent: point it at a task, it drives your mouse/keyboard.",
    )
    parser.add_argument("task", help="natural-language description of what to accomplish")
    parser.add_argument("--model", default=os.environ.get("SECDOGIE_MODEL", "claude-sonnet-5"))
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument(
        "--auto",
        action="store_true",
        help="execute actions without a y/N confirmation each step (only use on a machine/session you fully control)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ask the model for actions and log them, but never touch the mouse/keyboard",
    )
    parser.add_argument("--log-file", default=None, help="also append the run log to this file")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY environment variable is not set", file=sys.stderr)
        return 1

    provider = AnthropicProvider(model=args.model)
    config = AgentConfig(
        task=args.task,
        max_steps=args.max_steps,
        auto=args.auto,
        dry_run=args.dry_run,
        log_path=args.log_file,
    )
    return run(provider, config)


if __name__ == "__main__":
    sys.exit(main())
