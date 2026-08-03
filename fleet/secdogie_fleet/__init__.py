"""secdogie-fleet: run one agent per isolated desktop (a VM or its own user
session) and coordinate them from the host.

`open/` splits ONE machine's screen by window, so its agents share a single
mouse, keyboard and foreground window -- they think in parallel but act in a
queue. A fleet gives each task a desktop of its own, so they genuinely act at
once. See fleet/README.md.
"""
from .protocol import (
    Assign,
    Hello,
    ProtocolError,
    Result,
    Status,
    Stop,
    from_json,
    to_json,
)

__all__ = [
    "Assign",
    "Hello",
    "ProtocolError",
    "Result",
    "Status",
    "Stop",
    "from_json",
    "to_json",
]
