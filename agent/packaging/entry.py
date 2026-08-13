"""Entry point for the PyInstaller-built single-file executable.

PyInstaller freezes a *script*, not a console_scripts entry point, so this
tiny launcher just forwards to the same main() that the `secdogie-agent`
pip console script uses.

Order matters for the windowed Windows build (console=False):

  1. Install the safety nets (log file + crash dialog + parent-console
     reattach) FIRST — before importing the rest of the package.
  2. Only then import cli and run main().

If step 2 fails (missing module, bad bytecode, antivirus stripping a DLL,
…), the user still gets a visible error box and a secdogie.log next to the
exe instead of a silent flash-and-exit.
"""
from __future__ import annotations

import sys
import traceback


def _bootstrap() -> None:
    """Best-effort: install log + crash dialog before anything else can fail."""
    try:
        from secdogie_agent.frozen_runtime import bootstrap

        bootstrap()
    except Exception:
        pass  # never let the safety net itself block startup


def _report_fatal(text: str) -> None:
    """Write the failure somewhere durable and, on a windowed build, pop a box.

    Used when the failure happens before (or instead of) the normal excepthook,
    e.g. an ImportError while loading cli. Best-effort throughout.
    """
    where = None
    try:
        from secdogie_agent.frozen_runtime import log_path, _show_error_box

        where = log_path()
        with open(where, "a", encoding="utf-8", errors="replace") as f:
            f.write("\nSTARTUP FAILURE:\n" + text + "\n")
    except Exception:
        pass

    try:
        sys.stderr.write("\nSTARTUP FAILURE:\n" + text + "\n")
        sys.stderr.flush()
    except Exception:
        pass

    # No console on a double-clicked windowed exe — surface the error on screen.
    try:
        if where is not None:
            from secdogie_agent.frozen_runtime import _show_error_box

            _show_error_box(text, where)
        else:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            tail = text if len(text) <= 1800 else "...\n" + text[-1800:]
            messagebox.showerror(
                "secdogie-agent stopped",
                "secdogie-agent failed to start.\n\n" + tail,
            )
            root.destroy()
    except Exception:
        pass


def main() -> int:
    _bootstrap()
    try:
        from secdogie_agent.cli import main as cli_main

        return int(cli_main() or 0)
    except SystemExit as e:
        # argparse / explicit sys.exit — preserve the code, don't treat as crash.
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    except BaseException:
        _report_fatal("".join(traceback.format_exc()))
        return 1


if __name__ == "__main__":
    sys.exit(main())
