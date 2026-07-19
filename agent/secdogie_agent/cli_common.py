"""Shared CLI plumbing for the provider/model/API-key front door and the
agent-loop flags.

secdogie-agent, secdogie-android, and secdogie-ios are the same program with a
different backend bolted on, so they exposed an identical block of argparse
options plus identical key-resolution and error handling -- roughly forty lines
copied three times. This module owns that surface once: each tool adds its own
backend flags around these helpers instead of re-declaring the shared ones.

Kept deliberately small -- the pool-based scene3d CLI and the server-only open
CLI don't share this single-provider shape, so they don't use it."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod
from .providers import VisionProvider, make_provider

DEFAULT_MODEL = "claude-sonnet-5"


def add_provider_args(parser: argparse.ArgumentParser) -> None:
    """Add the provider / model / api-key / config / init-config flags every
    agent front-end shares."""
    parser.add_argument(
        "--model",
        default=None,
        help=f"vision model to use (default: {DEFAULT_MODEL}); the prefix picks the provider "
        "(claude-* -> Anthropic, gpt-*/o-series -> OpenAI), or use a provider/model ref like openai/gpt-5.5",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai"],
        default=None,
        help="force the provider instead of inferring it from the model id",
    )
    parser.add_argument("--api-key", default=None, help="API key for the chosen provider (overrides env/config)")
    parser.add_argument("--config", default=None, help="path to a config file to read the API key/model from")
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="write a template config file you can fill in with your API key, then exit",
    )


def add_loop_args(parser: argparse.ArgumentParser) -> None:
    """Add the agent-loop flags shared across the desktop/Android/iOS tools.
    Tool-specific extras (--macro, --gui, backend targeting) stay in each CLI."""
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="max frames/actions before stopping (default 50; 100000 in --watch mode)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="monitor mode: keep watching the screen and only act when the situation in the task occurs",
    )
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=2.0,
        help="minimum seconds between frames in --watch mode (default 2.0)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="execute actions without a y/N confirmation each step (only on a machine/device you fully control)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ask the model for actions and log them, but never touch the machine/device",
    )
    parser.add_argument(
        "--allow-risky",
        action="store_true",
        help="with --auto, also run high-risk actions (currently `open`, which launches a file/URL) "
        "without confirmation; by default those still prompt even under --auto",
    )
    parser.add_argument("--log-file", default=None, help="also append the run log to this file")
    parser.add_argument(
        "--max-image-edge",
        type=int,
        default=None,
        help="longest edge (px) of the screenshot sent to the model; lower = faster/cheaper, higher = more detail",
    )
    parser.add_argument(
        "--grid",
        action="store_true",
        help="overlay a labeled coordinate grid on the screenshot to help the model aim",
    )
    parser.add_argument(
        "--action-pause",
        type=float,
        default=None,
        help="seconds to wait after each action before the next screenshot, so the UI can react "
        "(default 0.4; lower is faster but risks acting on a stale frame; 0 disables)",
    )
    parser.add_argument(
        "--stall-limit",
        type=int,
        default=None,
        help="stop if the model repeats the same action against an unchanged screen this many times "
        "in a row -- the action isn't landing (default 4; 0 disables)",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="decompose the task into sub-tasks up front and work one at a time, carrying progress "
        "forward; a stuck sub-task is skipped after --subtask-step-limit steps instead of spinning",
    )
    parser.add_argument(
        "--subtask-step-limit",
        type=int,
        default=None,
        help="with --plan, skip a sub-task that runs this many steps without finishing (default 15; 0 disables)",
    )
    parser.add_argument(
        "--trace",
        default=None,
        metavar="PATH",
        help="write a tamper-evident hash-chained audit trace (frame hash + decision + result per step) "
        "to this JSONL file; verify later with `python -m secdogie_agent.trace <path>`",
    )
    parser.add_argument(
        "--memory",
        default=None,
        metavar="PATH",
        help="give the agent persistent cross-run memory in this SQLite file: it saves durable facts "
        "with a `remember` action and they're recalled into its prompt on later runs "
        "(plaintext on disk -- never have it store secrets)",
    )


def handle_init_config(args: argparse.Namespace, prog: str) -> int:
    """Run the --init-config flow: write a template config and print next
    steps. Returns the process exit code (0 ok, 1 if a config already exists)."""
    try:
        path = config_mod.write_template(Path(args.config) if args.config else None)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote config template to {path}")
    print(f'edit it and set your provider\'s API key, then run: {prog} "your task"')
    return 0


def resolve_provider(args: argparse.Namespace, prog: str) -> VisionProvider | None:
    """Resolve the provider/model/key from args + env + config and build the
    provider. On any failure, print an actionable error naming `prog` and
    return None so the caller can exit 1."""
    resolved = config_mod.resolve(
        cli_api_key=args.api_key,
        cli_model=args.model,
        config_path=args.config,
        cli_provider=args.provider,
    )
    if not resolved.api_key:
        print(
            f"error: no API key found for the {resolved.provider} provider. Provide one via "
            f"--api-key, the {resolved.env_var} environment variable, or a config file (run "
            f"`{prog} --init-config` to create one).",
            file=sys.stderr,
        )
        return None
    try:
        return make_provider(resolved.provider, resolved.model, resolved.api_key)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return None


def loop_config_kwargs(args: argparse.Namespace, *, task: str, backend=None) -> dict:
    """Build the AgentConfig kwargs shared by every tool from the loop flags.
    `backend` stays None for the desktop tool (the loop builds the desktop
    backend itself); Android/iOS pass their own. Tool-specific fields
    (macro_path, gui, ...) are added by the caller."""
    max_steps = args.max_steps if args.max_steps is not None else (100000 if args.watch else 50)
    kwargs: dict = dict(
        task=task,
        max_steps=max_steps,
        auto=args.auto,
        dry_run=args.dry_run,
        log_path=args.log_file,
        grid=args.grid,
        watch=args.watch,
        watch_interval=args.watch_interval,
    )
    if backend is not None:
        kwargs["backend"] = backend
    if args.max_image_edge is not None:
        kwargs["max_image_edge"] = args.max_image_edge
    if args.action_pause is not None:
        kwargs["action_pause"] = args.action_pause
    if args.stall_limit is not None:
        kwargs["stall_limit"] = args.stall_limit
    if getattr(args, "plan", False):
        kwargs["plan"] = True
    if getattr(args, "subtask_step_limit", None) is not None:
        kwargs["subtask_step_limit"] = args.subtask_step_limit
    if getattr(args, "trace", None) is not None:
        kwargs["trace_path"] = args.trace
    if getattr(args, "allow_risky", False):
        kwargs["confirm_high_risk"] = False
    if getattr(args, "memory", None) is not None:
        kwargs["memory_path"] = args.memory
    return kwargs
