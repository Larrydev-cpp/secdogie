"""Bridge the action-plan gate into the live agent loop (M3 wiring).

The agent loop accepts an injected ``plan_gate(view, recent) -> (allowed, note)``
hook and hands it plain-data views of the action it is about to execute and of
the last few actions. This module turns those views into ``PlannedAction`` s,
runs ``action_gate.gate`` with the node's granted capabilities, and decides.

Only *authorization* and *memory / intent* findings block inside the loop: a
missing capability, an instruction asking to post unattended, an action that
already failed repeatedly (consolidated memory), and a Gate 1 intent that is
missing or contradicts itself. The gate's heuristic findings (no-op,
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
    INTENT_CONTRADICTION,
    INTENT_UNPROVEN,
    KNOWN_FAILURE,
    OUT_OF_CAPABILITY,
    UNATTENDED_POSTING,
    UNAUTHORIZED_ACTION,
    GateContext,
    GateDecision,
    IntentContract,
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

BLOCKING = frozenset({OUT_OF_CAPABILITY, UNATTENDED_POSTING, KNOWN_FAILURE, INTENT_CONTRADICTION,
                      INTENT_UNPROVEN, UNAUTHORIZED_ACTION})

PlanGate = Callable[[dict, list], "tuple[bool, str]"]
GateObserver = Callable[[PlannedAction, GateDecision], None]


def to_planned(view: dict, *, purpose: str = "") -> PlannedAction:
    """A ``PlannedAction`` from the loop's plain-data action view. The Gate 1
    intent comes from the view's optional ``rollback`` / ``irreversible`` (the
    model states them) plus ``purpose`` (the caller knows which goal is
    running). ``irreversible`` counts only as a literal ``true``."""
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
        intent=IntentContract(
            purpose=purpose,
            rollback=str(view.get("rollback") or ""),
            irreversible=view.get("irreversible") is True,
        ),
    )


def make_plan_gate(capabilities: Iterable[str], *, enforce: bool = True, instruction: str = "",
                   known_failures: Iterable[str] = (), active_goal_ids: Iterable[str] = (),
                   purpose: str = "", require_intent: bool = False,
                   observer: GateObserver | None = None,
                   authorize: Callable[[PlannedAction], dict | None] | None = None,
                   operators=None, subject_did: str = "") -> PlanGate:
    """A loop hook enforcing ``capabilities`` (the node's current scopes, e.g.
    from ``secdogie_identity.capability.effective_scopes``).

    ``known_failures`` (consolidated memory), ``active_goal_ids`` and
    ``purpose`` / ``require_intent`` (Gate 1) are optional; left at their
    defaults the gate behaves exactly as before. ``observer(planned,
    decision)`` sees every judgment -- the run recorder uses it to tie a step
    to the action's effect hash.

    ``authorize(planned)`` is Gate 2's way to the operator: for a destructive
    action it is asked for an operator-signed token (the Dialogue App bridge
    sends a challenge and waits), and the gate then VERIFIES that token against
    ``operators`` and ``subject_did`` -- the bridge collects, the gate judges.
    With ``authorize`` set, a destructive action without a valid token is
    refused; a raising ``authorize`` counts as no token."""
    caps = frozenset(capabilities)
    known = frozenset(known_failures)
    active = frozenset(active_goal_ids)

    def plan_gate(view: dict, recent: list) -> tuple[bool, str]:
        planned = to_planned(view, purpose=purpose)
        token = None
        if authorize is not None and planned.destructive:
            try:
                token = authorize(planned)
            except Exception:  # noqa: BLE001 - no token is a refusal, never a pass
                token = None
        ctx = GateContext(
            capabilities=caps,
            enforce_capabilities=enforce,
            recent_actions=tuple(to_planned(r) for r in recent),
            requires_verification=False,
            instruction=instruction,
            require_intent=require_intent,
            active_goal_ids=active,
            known_failures=known,
            require_authorization=authorize is not None,
            authorization=token,
            operators=operators,
            subject_did=subject_did,
        )
        decision = gate(planned, ctx)
        if observer is not None:
            observer(planned, decision)
        if any(k in BLOCKING for k in decision.findings):
            return False, decision.reason
        return True, decision.reason

    return plan_gate


__all__ = ["BLOCKING", "PlanGate", "GateObserver", "make_plan_gate", "to_planned"]
