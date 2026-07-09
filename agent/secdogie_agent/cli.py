from __future__ import annotations

import argparse
import sys

from . import cli_common, dialog
from .loop import AgentConfig, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-agent",
        description="Vision-LLM computer-control agent: point it at a task, it drives your mouse/keyboard.",
    )
    parser.add_argument("task", nargs="?", help="natural-language description of what to accomplish")
    cli_common.add_provider_args(parser)
    cli_common.add_loop_args(parser)

    parser.add_argument(
        "--macro",
        default=None,
        metavar="PATH",
        help="RPA: replay this macro file with zero model calls, falling back to the live model the "
        "moment a step can't be resolved (e.g. the UI changed); a run that finishes successfully "
        "re-saves the full sequence here, so the next identical run gets faster/cheaper over time",
    )

    # Desktop-only input tuning + GUI dialogs.
    parser.add_argument("--move-duration", type=float, default=None, help="seconds to glide the cursor to a target")
    parser.add_argument("--settle", type=float, default=None, help="seconds to hover before clicking")
    parser.add_argument(
        "--gui",
        action="store_true",
        help="use GUI dialogs: enter the task in a window, review the model's plan before it acts, "
        "and answer its questions in a popup (needs tkinter; falls back to the terminal if unavailable)",
    )
    args = parser.parse_args(argv)

    if args.init_config:
        return cli_common.handle_init_config(args, "secdogie-agent")

    # GUI mode: verify we can actually show a window, else fall back gracefully.
    gui = args.gui
    if gui and not dialog.gui_available():
        print(
            "warning: --gui requested but no GUI is available (tkinter missing or no "
            "display); falling back to the terminal.",
            file=sys.stderr,
        )
        gui = False

    # If no task was given, prompt for it in a window (GUI) -- otherwise it's required.
    if not args.task:
        if gui:
            args.task = dialog.ask_task()
            if not args.task:
                print("cancelled: no task entered.")
                return 0
        else:
            parser.error("the following arguments are required: task")

    provider = cli_common.resolve_provider(args, "secdogie-agent")
    if provider is None:
        return 1

    # Desktop backend stays None so the loop builds it from move_duration/settle.
    cfg_kwargs = cli_common.loop_config_kwargs(args, task=args.task, backend=None)
    cfg_kwargs["gui"] = gui
    cfg_kwargs["macro_path"] = args.macro
    if args.move_duration is not None:
        cfg_kwargs["move_duration"] = args.move_duration
    if args.settle is not None:
        cfg_kwargs["settle"] = args.settle

    return run(provider, AgentConfig(**cfg_kwargs))


if __name__ == "__main__":
    sys.exit(main())
