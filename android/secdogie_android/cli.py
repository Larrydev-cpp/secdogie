from __future__ import annotations

import argparse
import sys

from secdogie_agent import cli_common
from secdogie_agent.loop import AgentConfig, run

from .adb import Adb
from .backend import AdbBackend


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-android",
        description="Vision-LLM Android-control agent: point it at a task, it drives a phone over adb.",
    )
    parser.add_argument("task", nargs="?", help="natural-language description of what to accomplish")
    cli_common.add_provider_args(parser)

    # adb target
    parser.add_argument(
        "--device",
        default=None,
        help="adb serial of the device to drive (needed when more than one is attached; see `adb devices`)",
    )
    parser.add_argument("--adb-path", default="adb", help="path to the adb binary (default: adb on PATH)")
    parser.add_argument(
        "--snap-to-elements",
        action="store_true",
        help="RPA-style targeting: snap each tap onto the real UI widget under it (read from the "
        "uiautomator hierarchy) instead of the raw pixel, for more reliable hits on buttons/controls",
    )

    cli_common.add_loop_args(parser)
    parser.add_argument(
        "--macro",
        default=None,
        metavar="PATH",
        help="RPA: replay this macro file with zero model calls, falling back to the live model the "
        "moment a step can't be resolved (e.g. the UI changed); a run that finishes successfully "
        "re-saves the full sequence here. Steps recorded on Android use the uiautomator hierarchy to "
        "re-find taps by element identity, not frozen coordinates -- independent of --snap-to-elements, "
        "which only affects live (non-replayed) taps.",
    )
    args = parser.parse_args(argv)

    if args.init_config:
        return cli_common.handle_init_config(args, "secdogie-android")

    if not args.task:
        parser.error("the following arguments are required: task")

    provider = cli_common.resolve_provider(args, "secdogie-android")
    if provider is None:
        return 1

    backend = AdbBackend(
        Adb(serial=args.device, adb_path=args.adb_path), snap_to_elements=args.snap_to_elements
    )
    cfg_kwargs = cli_common.loop_config_kwargs(args, task=args.task, backend=backend)
    cfg_kwargs["macro_path"] = args.macro
    return run(provider, AgentConfig(**cfg_kwargs))


if __name__ == "__main__":
    sys.exit(main())
