"""secdogie-console: a local web control console for a fleet coordinator.

Observe nodes and tasks and drive submit/stop/pause/resume from the browser,
with operator-DID-gated commands. Reuses the secdogie-open server pattern and
the secdogie-identity signing/allowlist.
"""
from __future__ import annotations

from .controller import ConsoleController
from .server import build_server, make_handler

__version__ = "0.5.0"

__all__ = ["ConsoleController", "build_server", "make_handler"]
