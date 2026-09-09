"""Shared operator-console palette.

Tk dialogs, the launcher, and the persistent HUD all draw from this so the
packaged exe is one visual language — dark industrial, no purple accent.
"""
from __future__ import annotations

import sys

BG = "#0a0b0d"
SURFACE = "#121418"
SURFACE_2 = "#191c21"
FG = "#ecece8"
MUTED = "#8b8d92"
SUBTLE = "#6a6d73"
ACCENT = "#c5cbd4"
ACCENT_FG = "#0a0b0d"
DENY = "#c48982"
OK = "#8fa38c"
WARN = "#c4b49a"
BORDER = "#2a2d33"


def font(size: int = 10, *, bold: bool = False, mono: bool = False):
    if mono:
        family = "Consolas" if sys.platform == "win32" else "monospace"
    elif sys.platform == "win32":
        family = "Segoe UI"
    elif sys.platform == "darwin":
        family = "Lucida Grande"
    else:
        family = "sans-serif"
    return (family, size, "bold") if bold else (family, size)
