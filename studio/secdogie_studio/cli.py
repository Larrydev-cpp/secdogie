"""``secdogie-studio``: the desktop app.

    secdogie-studio [--profile DIR]      open the window (first run: create keys)
    secdogie-studio --self-test          headless: profile, node, link, stop
    secdogie-studio --node-child ARGS    (frozen builds) run the bundled node

The window needs PySide6 (``pip install 'secdogie-studio[gui]'``) and a
display. Nothing is installed, started at login or left running: the node is
a child of the window and stops when it closes.
"""
from __future__ import annotations

import argparse
import os
import sys

from .profile import default_root


def _has_display() -> bool:
    if sys.platform in ("darwin", "win32") or os.environ.get("QT_QPA_PLATFORM"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="secdogie-studio", description="secdogie Studio: the desktop app.")
    p.add_argument("--profile", default=None, metavar="DIR",
                   help=f"profile directory (default: {default_root()})")
    p.add_argument("--self-test", action="store_true",
                   help="headless check of the local loop in a throwaway profile; exit 0 when it passes")
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--node-child"]:
        from secdogie_node.cli import main as node_main

        return node_main(argv[1:])
    args = build_parser().parse_args(argv)
    if args.self_test:
        from .selftest import run_self_test

        return run_self_test()
    if not _has_display():
        sys.stderr.write("secdogie-studio needs a display. On a headless machine run the node directly "
                         "(secdogie-node run ...) and operate it with secdogie-dialogue connect.\n")
        return 2
    try:
        import PySide6  # noqa: F401
    except ImportError:
        sys.stderr.write("the window needs PySide6: pip install 'secdogie-studio[gui]'\n")
        return 2
    from pathlib import Path

    from .app import run_gui

    return run_gui(Path(args.profile) if args.profile else default_root())


if __name__ == "__main__":
    sys.exit(main())
