"""The safety decision gate: what a serving node decides *before* it routes.

``SafetyLayer`` judges answers after they come back. ``DecisionGate`` stands
in front of the decision layer and judges the request itself, against hard
rules and against what this node has seen happen. Like the safety layer it is
deterministic -- no model sits in this path -- and its memory can only make it
**stricter**, the rule citadel's staged memory follows too: history may raise
the quorum a request needs or refuse it, never lower the quorum the user asked
for and never wave through what a hard rule refuses.

Three verdicts:

  * ``veto``     -- refused, ``vetoed: ...``. Hard rules, none of which can be
    turned off:
      - this node's own decision layer is latched;
      - the input carries a lease canary token (``sdgc_`` + 32 hex) -- probing
        for lease material;
      - an action-bearing request (scopes granted) from a user whose own signed
        request is the evidence in a held ``model.forbidden-capability``
        incident: the last time this user asked for an action, an upstream
        model answered with a capability no grant covers. It holds until an
        operator's reset names that incident.
  * ``escalate`` -- *cognitive intervention*: the request is routed, but its
    answers must be cross-checked -- the node needs ``escalate_quorum`` (2)
    agreeing answers from distinct model nodes, distinct models first, even if
    the user asked for one. For an action-bearing request when, inside
    ``window``, one of its models produced divergent or intercepted answers,
    a model node that could serve it had its breaker open, or the user's own
    requests were intercepted ``user_intercepts`` (3) times. A request pinned
    to fewer providers than that cannot be cross-checked without breaking its
    pins, so it is vetoed instead; one whose cross-check cannot be routed is
    refused ``vetoed: cross-check unavailable`` -- it fails closed.
  * ``allow``.

The absolute circuit breaker stays where it was: proven mutiny latches in
``mutiny.MutinyLedger`` and takes the decision layer off the data path
(drawing 3). The gate sits in front of it and keeps the decision layer from
walking into what history already shows.

``History`` is a bounded ring of what the node saw -- outcomes, divergent
answers, breakers opening, incidents -- by subject (``user:<did>``,
``model:<name>``, ``node:<did>``).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .lease import CANARY
from .mutiny import MODEL_FORBIDDEN_CAPABILITY, MutinyLedger, decider_target

ALLOW = "allow"
ESCALATE = "escalate"
VETO = "veto"

EV_ACCEPTED = "outcome.accepted"
EV_INTERCEPTED = "outcome.intercepted"
EV_DIVERGENT = "fault.divergent"
EV_BREAKER_OPEN = "breaker.open"


def user_subject(did: str) -> str:
    return f"user:{did}"


def model_subject(model: str) -> str:
    return f"model:{model}"


def node_subject(did: str) -> str:
    return f"node:{did}"


@dataclass(frozen=True)
class GateVerdict:
    status: str
    quorum: int
    reasons: tuple[str, ...] = ()


class History:
    """What this node saw happen, newest last; at most ``size`` events, and
    only those inside ``window`` seconds count."""

    def __init__(self, *, size: int = 4096, window: float = 3600.0):
        self.window = float(window)
        self._events: deque[tuple[float, str, str]] = deque(maxlen=int(size))

    def __len__(self) -> int:
        return len(self._events)

    def note(self, now: float, kind: str, subject: str) -> None:
        self._events.append((float(now), kind, subject))

    def count(self, kind: str, subject: str, now: float, window: float | None = None) -> int:
        since = now - (self.window if window is None else window)
        return sum(1 for t, k, s in self._events if t >= since and k == kind and s == subject)


class DecisionGate:
    def __init__(self, *, window: float = 3600.0, user_intercepts: int = 3, escalate_quorum: int = 2):
        self.window = float(window)
        self.user_intercepts = int(user_intercepts)
        self.escalate_quorum = int(escalate_quorum)

    def review(self, request: dict, *, me: str, ledger: MutinyLedger, history: History, now: float,
               providers=(), lockdown: bool = False) -> GateVerdict:
        """Judge ``request`` before any route is chosen. ``providers``: the
        model nodes that could serve it, for their breaker history."""
        quorum = int(request["quorum"])
        if ledger.excludes_node(me) or ledger.is_latched(decider_target(me)):
            return GateVerdict(VETO, quorum, ("this node is latched",))
        if CANARY.search(request["input"]):
            return GateVerdict(VETO, quorum, ("the input carries a lease canary token",))
        acting = bool(request["scopes"])
        if acting:
            for signed in ledger.signed_incidents():
                if signed.get("kind") != MODEL_FORBIDDEN_CAPABILITY:
                    continue
                asked = signed["evidence"].get("request") or {}
                if asked.get("user") == request["user"] and asked.get("scopes"):
                    return GateVerdict(VETO, quorum, (
                        "an earlier action request of this user drew a forbidden capability "
                        f"(incident {signed['incident_id'][:12]}); an operator must review it",))
        if lockdown or not acting:
            return GateVerdict(ALLOW, quorum)  # lockdown already cross-checks every request

        reasons = []
        for model in request["models"]:
            subject = model_subject(model)
            for kind in (EV_DIVERGENT, EV_INTERCEPTED):
                if history.count(kind, subject, now, self.window):
                    reasons.append(f"{kind} for {model}")
        for did in sorted(set(providers)):
            if history.count(EV_BREAKER_OPEN, node_subject(did), now, self.window):
                reasons.append(f"breaker opened for {did[-8:]}")
        intercepted = history.count(EV_INTERCEPTED, user_subject(request["user"]), now, self.window)
        if intercepted >= self.user_intercepts:
            reasons.append(f"{intercepted} of this user's requests intercepted")
        if not reasons:
            return GateVerdict(ALLOW, quorum)
        need = max(quorum, self.escalate_quorum)
        if request["pins"] and len(request["pins"]) < need:
            return GateVerdict(VETO, quorum, ("cross-check unavailable: pinned to fewer model nodes than it needs",
                                              *reasons))
        return GateVerdict(ESCALATE, need, tuple(reasons))


__all__ = [
    "ALLOW",
    "ESCALATE",
    "VETO",
    "EV_ACCEPTED",
    "EV_INTERCEPTED",
    "EV_DIVERGENT",
    "EV_BREAKER_OPEN",
    "GateVerdict",
    "History",
    "DecisionGate",
    "user_subject",
    "model_subject",
    "node_subject",
]
