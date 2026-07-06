from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod
from .loop import AgentConfig, run
from .providers.anthropic_provider import AnthropicProvider

DEFAULT_MODEL = "claude-sonnet-5"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-agent",
        description="Vision-LLM computer-control agent: point it at a task, it drives your mouse/keyboard.",
    )
    parser.add_argument("task", nargs="?", help="natural-language description of what to accomplish")
    parser.add_argument("--model", default=None, help=f"vision model to use (default: {DEFAULT_MODEL})")
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

    # API key / config file
    parser.add_argument("--api-key", default=None, help="Anthropic API key (overrides env var and config file)")
    parser.add_argument("--config", default=None, help="path to a config file to read the API key/model from")
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="write a template config file you can fill in with your API key, then exit",
    )

    # Accuracy / input tuning
    parser.add_argument(
        "--max-image-edge",
        type=int,
        default=None,
        help="longest edge (px) of the screenshot sent to the model; lower = faster/cheaper, higher = more detail",
    )
    parser.add_argument(
        "--grid",
        action="store_true",
        help="overlay a labeled coordinate grid on the screenshot to help the model aim clicks",
    )
    parser.add_argument("--move-duration", type=float, default=None, help="seconds to glide the cursor to a target")
    parser.add_argument("--settle", type=float, default=None, help="seconds to hover before clicking")
    args = parser.parse_args(argv)

    if args.init_config:
        try:
            path = config_mod.write_template(Path(args.config) if args.config else None)
        except FileExistsError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"wrote config template to {path}")
        print('edit it and set ANTHROPIC_API_KEY, then run: secdogie-agent "your task"')
        return 0

    if not args.task:
        parser.error("the following arguments are required: task")

    resolved = config_mod.resolve(
        cli_api_key=args.api_key, cli_model=args.model, config_path=args.config
    )
    if not resolved.api_key:
        print(
            "error: no API key found. Provide one via --api-key, the ANTHROPIC_API_KEY "
            "environment variable, or a config file (run `secdogie-agent --init-config` "
            "to create one).",
            file=sys.stderr,
        )
        return 1

    provider = AnthropicProvider(model=resolved.model or DEFAULT_MODEL, api_key=resolved.api_key)

    # Build AgentConfig, letting unset CLI options fall back to AgentConfig's defaults.
    cfg_kwargs: dict = dict(
        task=args.task,
        max_steps=args.max_steps,
        auto=args.auto,
        dry_run=args.dry_run,
        log_path=args.log_file,
        grid=args.grid,
    )
    if args.max_image_edge is not None:
        cfg_kwargs["max_image_edge"] = args.max_image_edge
    if args.move_duration is not None:
        cfg_kwargs["move_duration"] = args.move_duration
    if args.settle is not None:
        cfg_kwargs["settle"] = args.settle

    return run(provider, AgentConfig(**cfg_kwargs))


if __name__ == "__main__":
    sys.exit(main())
