"""Make the windowed (console=False) frozen build safe -- never a silent box.

A GUI-subsystem exe has no console, and PyInstaller sets sys.stdout/sys.stderr
to None. Left alone that means: an uncaught error vanishes with the window,
every print()/log write crashes on the None stream, and a terminal user who
runs `secdogie-agent.exe --help` sees nothing. `bootstrap()` wires the three
nets the windowed build needs, and is a **no-op when running from source**
(a dev already has a real console and Python's own traceback):

  1. Crash dialog -- an excepthook that always records the traceback and, when
     there's no console, pops a Windows error box, so a crash is visible instead
     of a disappearance.
  2. Log to disk -- stderr (where the agent logs) is tee'd to `secdogie.log`
     next to the exe; with no console, stdout is routed there too instead of the
     None sink, so there is always a post-mortem trail and nothing crashes on None.
  3. CLI still works -- launched from a terminal (i.e. with any argument, such as
     --help), it reattaches to the parent console so stdout/--help are visible.

Everything here is best-effort and swallows its own errors: the safety net must
never itself be the reason the program won't start.
"""
from __future__ import annotations

import sys
from pathlib import Path


class _Tee:
    """A write stream that forwards to several underlying streams, so the agent's
    output can go to the console AND to secdogie.log at once. Tolerant: a failing
    stream (e.g. a closed console) never breaks the others or the caller."""

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        # True if any real stream is a terminal, so argparse/isatty callers still
        # detect an interactive console when one is attached.
        for s in self._streams:
            try:
                if s.isatty():
                    return True
            except Exception:
                pass
        return False


def _exe_dir() -> Path | None:
    """Directory of the actual running executable for a frozen build (PyInstaller
    points sys.executable at the real .exe), or None from source. Self-contained
    so this safety net has no import dependencies that could themselves fail."""
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except OSError:
            return None
    return None


def log_path() -> Path:
    """Where secdogie.log goes: next to the exe for a frozen build (portable --
    the log stays with the app), falling back to ~/.secdogie then the temp dir if
    that folder isn't writable (e.g. the exe sits in Program Files)."""
    candidates: list[Path] = []
    exe_dir = _exe_dir()
    if exe_dir is not None:
        candidates.append(exe_dir / "secdogie.log")
    candidates.append(Path.home() / ".secdogie" / "secdogie.log")
    import tempfile

    candidates.append(Path(tempfile.gettempdir()) / "secdogie.log")
    for c in candidates:
        try:
            c.parent.mkdir(parents=True, exist_ok=True)
            return c
        except Exception:
            continue
    return candidates[-1]


def _attach_parent_console() -> bool:
    """Windows only: reattach a GUI-subsystem exe to the console it was launched
    from (`AttachConsole(ATTACH_PARENT_PROCESS)`), and point the std streams at
    it, so a terminal invocation (`... --help`) prints where the user can see it.
    Returns True only if a parent console was actually attached."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        ATTACH_PARENT_PROCESS = -1
        if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return False  # no parent console (a real double-click) -- stay windowed
        sys.stdout = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
        sys.stderr = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
        try:
            sys.stdin = open("CONIN$", encoding="utf-8", errors="replace")
        except Exception:
            pass
        return True
    except Exception:
        return False


def _stdio_is_console() -> bool:
    try:
        return sys.stdout is not None and sys.stdout.isatty()
    except Exception:
        return False


def _show_error_box(text: str, where: Path) -> None:
    """Pop a native error dialog with the traceback tail, for the no-console case
    where there's nowhere on screen for it to go otherwise. Best-effort."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        tail = text if len(text) <= 1800 else "...\n" + text[-1800:]
        messagebox.showerror(
            "secdogie-agent stopped",
            "secdogie-agent hit an error and had to close.\n\n"
            f"{tail}\n\nThe full log was written to:\n{where}",
        )
        root.destroy()
    except Exception:
        pass


def _install_excepthook(has_console: bool, where: Path) -> None:
    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)  # Ctrl-C isn't a crash to report
            return
        import traceback

        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            sys.stderr.write("\nUNCAUGHT EXCEPTION:\n" + text)
            sys.stderr.flush()
        except Exception:
            pass
        if not has_console:
            _show_error_box(text, where)

    sys.excepthook = hook


def bootstrap() -> None:
    """Install the windowed-build safety nets. No-op unless this is a frozen
    (packaged) build, so running from source keeps normal Python behaviour."""
    if not getattr(sys, "frozen", False):
        return

    attached = _attach_parent_console()
    has_console = attached or _stdio_is_console()

    where = log_path()
    try:
        logfile = open(where, "a", buffering=1, encoding="utf-8", errors="replace")
    except Exception:
        logfile = None

    if logfile is not None:
        if has_console:
            # Visible in the terminal AND kept on disk for later.
            sys.stdout = _Tee(sys.stdout, logfile)
            sys.stderr = _Tee(sys.stderr, logfile)
        else:
            # No console: the None sink would crash print()/logging, so send it
            # all to the file -- the only place it can go.
            sys.stdout = logfile
            sys.stderr = logfile

    _install_excepthook(has_console, where)
