from __future__ import annotations

import argparse
import sys

from secdogie_agent import cli_common
from secdogie_agent.loop import AgentConfig, run

from .backend import IosBackend
from .wda import Wda

DEFAULT_WDA_URL = "http://127.0.0.1:8100"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-ios",
        description="Vision-LLM iOS-control agent: drives an iPhone/iPad through WebDriverAgent.",
    )
    parser.add_argument("task", nargs="?", help="natural-language description of what to accomplish")
    cli_common.add_provider_args(parser)

    # WebDriverAgent target
    parser.add_argument(
        "--wda-url",
        default=DEFAULT_WDA_URL,
        help=f"base URL of the running WebDriverAgent server (default: {DEFAULT_WDA_URL}); "
        "forward it from the device with `iproxy 8100 8100` -- see ios/README.md",
    )
    parser.add_argument(
        "--humanize-taps",
        action="store_true",
        help="issue each single tap as a short randomized-duration touchAndHold instead of the "
        "instantaneous /wda/tap. Changes tap timing signature only -- see ios/README.md for what "
        "this does and does not change (double-tap is unaffected)",
    )

    cli_common.add_loop_args(parser)
    args = parser.parse_args(argv)

    if args.init_config:
        return cli_common.handle_init_config(args, "secdogie-ios")

    if not args.task:
        parser.error("the following arguments are required: task")

    provider = cli_common.resolve_provider(args, "secdogie-ios")
    if provider is None:
        return 1

    backend = IosBackend(Wda(base_url=args.wda_url), humanize_taps=args.humanize_taps)
    cfg_kwargs = cli_common.loop_config_kwargs(args, task=args.task, backend=backend)
    return run(provider, AgentConfig(**cfg_kwargs))


if __name__ == "__main__":
    sys.exit(main())
