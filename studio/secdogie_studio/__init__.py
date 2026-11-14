"""secdogie Studio: the desktop app for Windows, macOS and Linux (G1).

A PySide6 window that is one more operator App, not a new trust root. It
creates this machine's keys on first run, runs ``secdogie-node`` as a child that
stops with the window, and talks to it over the same DID-authenticated
dialogue the Dialogue App uses. See PLAN.md (track G).
"""
from __future__ import annotations

__version__ = "0.1.0"
