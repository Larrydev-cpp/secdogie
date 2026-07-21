from __future__ import annotations

import argparse
import sys

from . import cli_common, dialog, launcher_menu
from .loop import AgentConfig, run


def main(argv: list[str] | None = None) -> int:
    # One-file UX: a packaged exe double-clicked with no arguments shows the
    # frosted-glass chooser and runs whatever card was picked; closing it exits.
    # Any explicit argument (terminal, script) skips the menu entirely.
    if argv is None:
        argv = sys.argv[1:]
    if launcher_menu.should_offer(argv):
        chosen = launcher_menu.show_menu()
        if chosen is None:
            return 0
        argv = chosen

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

    # Programmable skills: run an authored JSON skill library (sub-flows, if/while,
    # loops, params) instead of a one-off task. See agent/README.md.
    parser.add_argument("--skill", default=None, metavar="PATH", help="run a programmable skill library (JSON) instead of a task")
    parser.add_argument("--skill-entry", default=None, metavar="NAME", help="which skill in the library to run (default: main)")
    parser.add_argument(
        "--skill-arg", action="append", default=[], metavar="K=V",
        help="bind a skill parameter (repeatable), e.g. --skill-arg user=alice",
    )

    # Desktop-only input tuning + GUI dialogs.
    parser.add_argument("--move-duration", type=float, default=None, help="seconds to glide the cursor to a target")
    parser.add_argument("--settle", type=float, default=None, help="seconds to hover before clicking")
    parser.add_argument(
        "--desktop-ax",
        action="store_true",
        help="make the desktop element-aware via the OS accessibility tree (UI Automation on Windows), "
        "so --macro can anchor clicks to a widget's identity instead of pixels; needs the platform "
        "accessibility library (Windows: `pip install uiautomation`), and no-ops with a hint without it",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="use GUI dialogs: enter the task in a window, review the model's plan before it acts, "
        "and answer its questions in a popup (needs tkinter; falls back to the terminal if unavailable)",
    )
    args = parser.parse_args(argv)

    if args.init_config:
        return cli_common.handle_init_config(args, "secdogie-agent")

    # Programmable skills run their own interpreter instead of the task loop.
    if args.skill:
        provider = cli_common.resolve_provider(args, "secdogie-agent")
        if provider is None:
            return 1
        skill_args = {}
        for kv in args.skill_arg:
            if "=" not in kv:
                parser.error(f"--skill-arg must be K=V, got {kv!r}")
            k, v = kv.split("=", 1)
            skill_args[k] = v
        from .skill_runner import run_skill_file

        return run_skill_file(
            provider, args.skill, args.skill_entry or "main", skill_args, auto=args.auto
        )

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
    cfg_kwargs["desktop_ax"] = args.desktop_ax
    if args.move_duration is not None:
        cfg_kwargs["move_duration"] = args.move_duration
    if args.settle is not None:
        cfg_kwargs["settle"] = args.settle

    return run(provider, AgentConfig(**cfg_kwargs))


if __name__ == "__main__":
    sys.exit(main())
