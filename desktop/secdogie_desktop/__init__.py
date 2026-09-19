"""secdogie-desktop: a native window (tkinter) to control a secdogie fleet.

A single desktop application -- live nodes/tasks and submit/stop/pause/resume --
over the same ConsoleController the web console uses. The pure presentation
logic lives in `viewmodel` (tk-free, unit-tested); `app.FleetWindow` is the view.
"""
from __future__ import annotations

from . import viewmodel

__version__ = "0.5.0"

__all__ = ["viewmodel"]
