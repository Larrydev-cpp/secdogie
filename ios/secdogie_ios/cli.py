from __future__ import annotations

import argparse
import sys
from pathlib import Path

from secdogie_agent import config as config_mod
from secdogie_agent.loop import AgentConfig, run
from secdogie_agent.providers import make_provider

from .backend import IosBackend
from .wda import Wda

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_WDA_URL = "http://127.0.0.1:8100"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-ios",
        description="Vision-LLM iOS-control agent: drives an iPhone/iPad through WebDriverAgent.",
    )
    parser.add_argument("task", nargs="?", help="natural-language description of what to accomplish")

    # Provider / model / key -- shared with secdogie-agent's config resolution.
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

    # WebDriverAgent target
    parser.add_argument(
        "--wda-url",
        default=DEFAULT_WDA_URL,
        help=f"base URL of the running WebDriverAgent server (default: {DEFAULT_WDA_URL}); "
        "forward it from the device with `iproxy 8100 8100` -- see ios/README.md",
    )

    # Loop behavior -- same semantics as secdogie-agent.
    parser.add_argument("--max-steps", type=int, default=None, help="max frames/actions before stopping (default 50; 100000 in --watch)")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="monitor mode: keep watching the screen and only act when the situation in the task occurs",
    )
    parser.add_argument("--watch-interval", type=float, default=2.0, help="minimum seconds between frames in --watch (default 2.0)")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="execute actions without a y/N confirmation each step (only on a device you fully control)",
    )
    parser.add_argument("--dry-run", action="store_true", help="ask the model for actions and log them, but never touch the device")
    parser.add_argument("--log-file", default=None, help="also append the run log to this file")
    parser.add_argument(
        "--max-image-edge",
        type=int,
        default=None,
        help="longest edge (px) of the screenshot sent to the model; lower = faster/cheaper, higher = more detail",
    )
    parser.add_argument("--grid", action="store_true", help="overlay a labeled coordinate grid to help the model aim taps")
    args = parser.parse_args(argv)

    if args.init_config:
        try:
            path = config_mod.write_template(Path(args.config) if args.config else None)
        except FileExistsError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"wrote config template to {path}")
        print('edit it and set your provider\'s API key, then run: secdogie-ios "your task"')
        return 0

    if not args.task:
        parser.error("the following arguments are required: task")

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
            "`secdogie-ios --init-config` to create one).",
            file=sys.stderr,
        )
        return 1

    try:
        provider = make_provider(resolved.provider, resolved.model, resolved.api_key)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    backend = IosBackend(Wda(base_url=args.wda_url))

    max_steps = args.max_steps if args.max_steps is not None else (100000 if args.watch else 50)

    cfg_kwargs: dict = dict(
        task=args.task,
        max_steps=max_steps,
        auto=args.auto,
        dry_run=args.dry_run,
        log_path=args.log_file,
        grid=args.grid,
        watch=args.watch,
        watch_interval=args.watch_interval,
        backend=backend,
    )
    if args.max_image_edge is not None:
        cfg_kwargs["max_image_edge"] = args.max_image_edge

    return run(provider, AgentConfig(**cfg_kwargs))


if __name__ == "__main__":
    sys.exit(main())
