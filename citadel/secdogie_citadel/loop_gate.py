"""Bridge the action-plan gate into the live agent loop (M3 wiring).

The agent loop accepts an injected ``plan_gate(view, recent) -> (allowed, note)``
hook and hands it plain-data views of the action it is about to execute and of
the last few actions. This module turns those views into ``PlannedAction`` s,
runs ``action_gate.gate`` with the node's granted capabilities, and decides.

Only *authorization* findings block inside the loop: a missing capability, or an
instruction asking to post unattended. The gate's heuristic findings (no-op,
repeated, polling, destructive chain, cost) are returned as a note but do not
block here, because they cannot see whether the screen changed -- pressing Down
twice or scrolling twice is normal -- and the loop already has its own
frame-hash stall detection plus per-step human confirmation for high-risk steps.
Verification is also left to the loop (it pixel-diffs / AX-checks every mutating
step), so ``requires_verification`` is off.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from .action_gate import (
    OUT_OF_CAPABILITY,
    UNATTENDED_POSTING,
    GateContext,
    PlannedAction,
    gate,
)

# Agent action names -> the gate's action vocabulary. Pointer actions (including
# hover/move) fall under click. Unknown names pass through unchanged, so under
# enforcement an unknown mutating action has no scope and is refused.
_KIND_MAP = {
    "left_click": "click",
    "right_click": "click",
    "double_click": "click",
    "click_element": "click",
    "track_click": "click",
    "move": "click",
    "drag": "drag",
    "type": "type",
    "key": "key",
    "hold_key": "key",
    "scroll": "scroll",
    "open": "open",
    "run_elevated": "run_elevated",
    "wait": "wait",
    "screenshot": "screenshot",
    "look": "observe",
}

BLOCKING = frozenset({OUT_OF_CAPABILITY, UNATTENDED_POSTING})

PlanGate = Callable[[dict, list], "tuple[bool, str]"]


def to_planned(view: dict) -> PlannedAction:
    """A ``PlannedAction`` from the loop's plain-data action view."""
    raw_kind = str(view.get("kind") or "")
    element = view.get("element")
    x, y = view.get("x"), view.get("y")
    if element:
        target = f"element:{element}"
    elif x is not None and y is not None:
        target = f"xy:{x},{y}"
    else:
        target = ""
    text = str(view.get("text") or "")
    if not text and view.get("keys"):
        text = "+".join(str(k) for k in view["keys"])
    if not text and view.get("path"):
        text = str(view["path"])
    return PlannedAction(
        kind=_KIND_MAP.get(raw_kind, raw_kind),
        target_id=target,
        text=text,
        high_risk=bool(view.get("high_risk")),
    )


def make_plan_gate(capabilities: Iterable[str], *, enforce: bool = True, instruction: str = "") -> PlanGate:
    """A loop hook enforcing ``capabilities`` (the node's current scopes, e.g.
    from ``secdogie_identity.capability.effective_scopes``)."""
    caps = frozenset(capabilities)

    def plan_gate(view: dict, recent: list) -> tuple[bool, str]:
        ctx = GateContext(
            capabilities=caps,
            enforce_capabilities=enforce,
            recent_actions=tuple(to_planned(r) for r in recent),
            requires_verification=False,
            instruction=instruction,
        )
        decision = gate(to_planned(view), ctx)
        if any(k in BLOCKING for k in decision.findings):
            return False, decision.reason
        return True, decision.reason

    return plan_gate


__all__ = ["BLOCKING", "PlanGate", "make_plan_gate", "to_planned"]
