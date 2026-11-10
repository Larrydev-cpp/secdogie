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


def _ensure_stdio() -> None:
    """A windowed (no-console) frozen build can start with sys.stdin/stdout set
    to None even when its parent handed it pipes. The node child talks to Studio
    over exactly those pipes, so bind them back to the inherited descriptors."""
    for name, fd, mode in (("stdin", 0, "r"), ("stdout", 1, "w"), ("stderr", 2, "w")):
        if getattr(sys, name) is not None:
            continue
        try:
            setattr(sys, name, open(fd, mode, encoding="utf-8", buffering=1 if mode == "w" else -1, closefd=False))
        except OSError:
            if sys.platform != "win32":
                continue
            import ctypes
            import msvcrt

            handle = ctypes.windll.kernel32.GetStdHandle((-10, -11, -12)[fd])
            if handle in (0, -1):
                continue
            flags = os.O_RDONLY if mode == "r" else os.O_WRONLY
            with_fd = msvcrt.open_osfhandle(handle, flags)
            setattr(sys, name, open(with_fd, mode, encoding="utf-8", buffering=1 if mode == "w" else -1,
                                    closefd=False))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--node-child"]:
        if getattr(sys, "frozen", False):
            _ensure_stdio()
        from secdogie_node.cli import main as node_main

        return node_main(argv[1:])
    args = build_parser().parse_args(argv)
    if args.self_test:
        if getattr(sys, "frozen", False):
            _ensure_stdio()  # so a windowed build's result lines still reach a log
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
