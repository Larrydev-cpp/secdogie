"""The user's end: sign the request, judge the answers yourself.

``UserClient`` trusts no node with the verdict. Every answer that comes back
goes through its own ``SafetyLayer`` -- signatures, leases (learned from the
answers themselves and from directories, always broker-verified), the user's
quorum, the deterministic screens -- so a serving node that lies about what the
model said, or a forwarder that swaps an answer, achieves nothing but a refusal.

**Normal and degraded.** The request goes to an entry -- a serving node the user
is attached to -- or to ``fanout`` of them at once (the diagram's user is linked
to two). A refusal or a timeout moves on to the next entry. A refusal that only
says "not now" (``no route``, ``no quorum``, ``busy``) from the last untried entry waits
``retry_backoff`` first: in drawing 2 the mesh needs a few seconds to route
around a failed node, and asking again at once would only spend the tries.

**Lockdown.** Once the client holds a verified incident it stops using serving
nodes: the decision layer is suspended. It picks a standby forwarder, fetches a
directory if it lacks leases, **pins** the providers itself -- at least two,
distinct nodes, distinct models first, deterministic order -- signs a request
with quorum two, and accepts only answers that agree. A pinned node that does
not answer is dropped from the next try.

Evidence the client finds is signed as an incident and published through its
attached nodes, like anyone's. Outcomes are final: ``accepted`` (with the
output), ``intercepted`` (the answer was adversarial or the model nodes
disagreed -- nothing is delivered) or ``refused`` (no quorum, no route, out of
tries).
"""
from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from .mutiny import make_incident, model_target
from .participant import Config, Participant
from .protocol import (
    LOCKDOWN_QUORUM,
    MODE_LOCKDOWN,
    MODE_NORMAL,
    digest,
    make_request,
)
from .safety import ACCEPTED, INTERCEPTED, SafetyLayer, default_agree
from .session import (
    ASK_EDGES,
    AWAITING_DIR,
    OUTCOME_ABORTED,
    OUTCOME_EXPIRED,
    SUBMITTED,
    Session,
    SessionTable,
)

HELLO_EVERY = 30.0  # re-attach to entries and standby, refreshing leases and incidents
# Refusals that say "not now" rather than "no": the mesh may be repairing
# itself (drawing 2) or the entry may be full, so wait before asking again.
TRANSIENT = ("no route", "no quorum", "busy")

OUTCOME_ACCEPTED = "accepted"
OUTCOME_INTERCEPTED = "intercepted"
OUTCOME_REFUSED = "refused"


@dataclass(frozen=True)
class Outcome:
    status: str
    output: str | None = None
    providers: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    mode: str = MODE_NORMAL
    via: str | None = None


@dataclass(eq=False)
class _Ask(Session):
    """One ask: a session (``session.py``) keyed by its handle."""

    models: tuple[str, ...] = ()
    text: str = ""
    scopes: tuple[str, ...] = ()
    quorum: int = 1
    ttl: float = 60.0
    request: dict | None = None
    rhash: str = ""
    mode: str = MODE_NORMAL
    tries: int = 0
    deadline: float = 0.0                             # the current try times out
    tried: list[str] = field(default_factory=list)
    awaiting: set[str] = field(default_factory=set)   # nodes the current request went to, yet to refuse
    unresponsive: set[str] = field(default_factory=set)
    waiting_dir: bool = False
    last_reason: str = ""

    def release(self) -> None:
        self.request, self.text = None, ""
        self.tried, self.awaiting, self.unresponsive = [], set(), set()


class UserClient(Participant):
    beats = False

    def __init__(self, identity, wire, trust, *, entries, standby=(), fanout: int = 1, timeout: float = 15.0,
                 max_tries: int = 4, retry_backoff: float = 2.0, max_open: int = 256, retain: int = 1024,
                 retain_for: float = 300.0, agree: Callable[[str, str], bool] = default_agree,
                 config: Config | None = None, clock=time.time):
        super().__init__(identity, wire, trust, config=config, clock=clock)
        if not trust.users.contains(self.did):
            raise ValueError("a user client must be on the users list")
        self.entries = list(entries)
        self.standby = list(standby)
        self.fanout = max(1, int(fanout))
        self.timeout = float(timeout)
        self.max_tries = int(max_tries)
        self.retry_backoff = float(retry_backoff)
        self.safety = SafetyLayer(trust, self.leases, self.ledger, agree=agree)
        # open asks, and the outcomes of settled ones until taken, ``retain_for`` passes,
        # or ``retain`` newer ones push them out
        self.asks: SessionTable[_Ask] = SessionTable(capacity=max_open, retain_count=retain, retain_bytes=8 << 20,
                                                     retain_for=retain_for, edges=ASK_EDGES)
        self._by_rhash: dict[str, str] = {}
        self._late: OrderedDict[str, dict] = OrderedDict()  # rhash -> request, for asks already decided
        self._next_hello = 0.0
        for did in self.entries + self.standby:
            self.connect(did)
        self._dispatch.update({"result": self._on_result, "refused": self._on_refused})

    # -- the API ----------------------------------------------------------------------

    def ask(self, models, text: str, *, scopes=(), quorum: int = 1, ttl: float = 60.0) -> str:
        """Start a request; returns a handle for ``outcome`` / ``take``."""
        with self._lock:
            now = float(self.clock())
            handle = secrets.token_hex(8)
            life = (self.timeout + self.retry_backoff) * (self.max_tries + 1)  # a backstop; tries end sooner
            ask = _Ask(handle, self.did, now, now + life, models=tuple(models), text=text, scopes=tuple(scopes),
                       quorum=int(quorum), ttl=float(ttl))
            if not self.asks.admit(ask):
                self.asks.remember(handle, Outcome(OUTCOME_REFUSED, reasons=(
                    f"busy: {len(self.asks)} asks already open",)), now=now)
                return handle
            self._submit(handle, now)
            return handle

    def outcome(self, handle: str) -> Outcome | None:
        """The outcome, once there is one (None while the ask is open, and
        after it was taken, forgotten or aged out)."""
        with self._lock:
            return None if self.asks.live(handle) is not None else self.asks.final(handle)

    def take(self, handle: str) -> Outcome | None:
        """The outcome, forgotten as it is handed over. None while still open."""
        with self._lock:
            return None if self.asks.live(handle) is not None else self.asks.forget(handle)

    def forget(self, handle: str) -> None:
        """Drop an ask: cancel it if still open, forget its outcome if settled."""
        with self._lock:
            ask = self.asks.live(handle)
            if ask is not None:
                self._finish(handle, Outcome(OUTCOME_REFUSED, reasons=("cancelled",), mode=ask.mode),
                             label=OUTCOME_ABORTED)
            self.asks.forget(handle)

    # -- submitting -------------------------------------------------------------------

    def _submit(self, handle: str, now: float) -> None:
        ask = self.asks.live(handle)
        if ask is None:
            return
        if ask.tries >= self.max_tries:
            reasons = ("out of tries",) + ((ask.last_reason,) if ask.last_reason else ())
            self._finish(handle, Outcome(OUTCOME_REFUSED, reasons=reasons, mode=ask.mode))
            return
        if self.ledger.lockdown:
            self._submit_lockdown(handle, ask, now)
        else:
            self._submit_normal(handle, ask, now)

    def _sign(self, handle: str, ask: _Ask, **kw) -> dict:
        if ask.rhash:
            self._by_rhash.pop(ask.rhash, None)
        ask.request = make_request(self.identity, ask.models, ask.text, scopes=ask.scopes, ttl=ask.ttl,
                                   clock=self.clock, **kw)
        ask.rhash = digest(ask.request)
        self._by_rhash[ask.rhash] = handle
        ask.tries += 1
        ask.deadline = float(self.clock()) + self.timeout
        return ask.request

    def _submit_normal(self, handle: str, ask: _Ask, now: float) -> None:
        usable = [e for e in self.entries if not self.ledger.excludes_node(e)]
        fresh = [e for e in usable if e not in ask.tried] or usable
        if not fresh:
            self._finish(handle, Outcome(OUTCOME_REFUSED, reasons=("no usable entry",), mode=MODE_NORMAL))
            return
        ask.mode = MODE_NORMAL
        request = self._sign(handle, ask, quorum=ask.quorum)
        ask.awaiting = set(fresh[:self.fanout])
        self.asks.advance(ask, SUBMITTED)
        for entry in fresh[:self.fanout]:
            ask.tried.append(entry)
            self.send(entry, "submit", request=request)

    def _forwarder(self, avoid=frozenset()) -> str | None:
        """The first standby forwarder in good standing: never a decider, since
        lockdown is the decision layer suspended."""
        for did in self.standby:
            if did not in avoid and not self.ledger.excludes_node(did) and not self.trust.deciders.contains(did):
                return did
        return None

    def _submit_lockdown(self, handle: str, ask: _Ask, now: float) -> None:
        ask.mode = MODE_LOCKDOWN
        self._by_rhash.pop(ask.rhash, None)  # whatever the old request still gets back is moot
        ask.awaiting = set()
        forwarder = self._forwarder(ask.unresponsive)
        if forwarder is None:
            self._finish(handle, Outcome(OUTCOME_REFUSED, reasons=("lockdown: no standby forwarder",),
                                         mode=MODE_LOCKDOWN))
            return
        quorum = max(ask.quorum, LOCKDOWN_QUORUM)
        pins = self.pick_pins(ask.models, quorum, now, avoid=ask.unresponsive)
        if len(pins) < quorum:
            if ask.waiting_dir:
                self._finish(handle, Outcome(OUTCOME_REFUSED, mode=MODE_LOCKDOWN, reasons=(
                    f"lockdown: {len(pins)} of {quorum} independent model nodes available",)))
                return
            ask.waiting_dir = True
            ask.deadline = now + self.timeout
            ask.tried.append(forwarder)
            self.asks.advance(ask, AWAITING_DIR)
            self.send(forwarder, "dir?")
            return
        ask.waiting_dir = False
        request = self._sign(handle, ask, quorum=quorum, pins=pins)
        ask.tried.append(forwarder)
        ask.awaiting = {forwarder}
        self.asks.advance(ask, SUBMITTED)
        for incident in self.ledger.signed_incidents()[-2:]:
            self.send(forwarder, "flood", obj=incident)  # the proof travels ahead of the request
        self.send(forwarder, "submit", request=request)
        self.note("lockdown-submit", via=forwarder[-8:], pins=[p[-8:] for p, _ in pins])

    def pick_pins(self, models, quorum: int, now: float, *, avoid=frozenset()) -> list[tuple[str, str]]:
        """Distinct model nodes holding a current lease, not latched, distinct
        models first; deterministic so the choice is auditable."""
        order = {m: i for i, m in enumerate(sorted(set(models)))}
        cands = sorted(((order[m], p, m) for p, m, _ in self.leases.eligible(models, now)
                        if p not in avoid and not self.ledger.excludes_node(p)
                        and not self.ledger.is_latched(model_target(m))))
        pins: list[tuple[str, str]] = []
        for distinct in (True, False):
            for _i, p, m in cands:
                if len(pins) >= quorum:
                    return pins
                if any(p == q for q, _ in pins) or (distinct and any(m == n for _, n in pins)):
                    continue
                pins.append((p, m))
        return pins

    # -- answers ------------------------------------------------------------------------

    def _on_result(self, src: str, f: dict) -> None:
        now = float(self.clock())
        rhash = f.get("request_hash")
        items = [tuple(x) for x in (f.get("items") or []) if isinstance(x, list) and len(x) == 2
                 and isinstance(x[0], dict) and isinstance(x[1], dict)]
        for _d, r in items:
            self.leases.add(r.get("lease"))
        handle = self._by_rhash.get(rhash)
        ask = self.asks.live(handle) if handle else None
        if ask is None:
            late = self._late.get(rhash)
            if late is not None:  # decided already, but a second path's answers are still evidence
                self._publish_evidence(self.safety.adjudicate(late, items, now=now).evidence)
            return
        quorum = max(ask.request["quorum"], LOCKDOWN_QUORUM) if ask.mode == MODE_LOCKDOWN else ask.request["quorum"]
        verdict = self.safety.adjudicate(ask.request, items, now=now, quorum=quorum)
        if verdict.evidence:
            self._finish(handle, Outcome(OUTCOME_INTERCEPTED, None, (), verdict.reasons, ask.mode, src))
            self._publish_evidence(verdict.evidence)
        elif verdict.status == ACCEPTED:
            self._finish(handle, Outcome(OUTCOME_ACCEPTED, verdict.output, verdict.providers, verdict.reasons,
                                         ask.mode, src))
        elif verdict.status == INTERCEPTED:
            self._finish(handle, Outcome(OUTCOME_INTERCEPTED, None, (), verdict.reasons, ask.mode, src))
        elif ask.mode == MODE_LOCKDOWN:
            answered = {r.get("provider") for _d, r in items}
            ask.unresponsive |= {p for p, _m in ask.request["pins"] if p not in answered}
            self._submit(handle, now)
        elif src in ask.awaiting:
            # The decider called it accepted; what it sent does not verify here. Move on.
            ask.awaiting.discard(src)
            ask.last_reason = f"the answers relayed by {src} did not verify"
            self.note("unverified", via=src[-8:], reasons=list(verdict.reasons)[:3])
            if not ask.awaiting:
                self._submit(handle, now)

    def _on_refused(self, src: str, f: dict) -> None:
        handle = self._by_rhash.get(f.get("request_hash"))
        ask = self.asks.live(handle) if handle else None
        if ask is None or src not in ask.awaiting:
            return
        ask.awaiting.discard(src)
        reason = f.get("reason") if isinstance(f.get("reason"), str) else ""
        ask.last_reason = reason
        self.note("refused", via=src[-8:], reason=reason)
        if reason.startswith("intercepted"):
            self._finish(handle, Outcome(OUTCOME_INTERCEPTED, reasons=(reason,), mode=ask.mode, via=src))
            return
        if reason.startswith("lockdown") and ask.mode != MODE_LOCKDOWN:
            # Stricter is always safe to assume; the incident proving it is on its way.
            self._submit_lockdown(handle, ask, float(self.clock()))
            return
        if ask.awaiting:
            return  # another path may still answer
        if ask.mode == MODE_LOCKDOWN and "not eligible" in reason:
            ask.unresponsive.add(reason.rsplit(" ", 1)[-1])
        now = float(self.clock())
        if ask.mode != MODE_LOCKDOWN and reason.startswith(TRANSIENT) and self._all_tried(ask):
            ask.deadline = now + self.retry_backoff  # on_tick asks again once it passes
            return
        self._submit(handle, now)

    def _all_tried(self, ask: _Ask) -> bool:
        return all(e in ask.tried for e in self.entries if not self.ledger.excludes_node(e))

    def on_directory(self, src: str) -> None:
        now = float(self.clock())
        for ask in self.asks.sessions():
            if ask.waiting_dir and self.asks.live(ask.key) is ask:
                self._submit_lockdown(ask.key, ask, now)

    def on_incident(self, incident) -> None:
        if not self.ledger.lockdown:
            return  # an isolated node is routed around by the mesh; nothing to redo here
        now = float(self.clock())
        for ask in self.asks.sessions():
            if ask.mode != MODE_LOCKDOWN and self.asks.live(ask.key) is ask:
                self._submit(ask.key, now)

    def on_tick(self, now: float) -> None:
        if now >= self._next_hello:
            self._next_hello = now + HELLO_EVERY
            for did in self.entries + self.standby:
                self.send(did, "dir?")  # attach, so incidents reach us before we ask anything
        for ask in self.asks.expired(now):
            if self.asks.live(ask.key) is ask:
                self._finish(ask.key, Outcome(OUTCOME_REFUSED, reasons=("expired",), mode=ask.mode),
                             label=OUTCOME_EXPIRED)
        for ask in self.asks.sessions():
            if self.asks.live(ask.key) is ask and now >= ask.deadline:
                if ask.mode == MODE_LOCKDOWN and ask.tried:
                    ask.unresponsive.add(ask.tried[-1])  # the forwarder itself went quiet
                    ask.waiting_dir = False
                self._submit(ask.key, now)
        self.asks.prune(now)

    def _finish(self, handle: str, outcome: Outcome, *, label: str | None = None) -> None:
        """Close the ask: its outcome is kept (bounded) until taken or aged out."""
        ask = self.asks.live(handle)
        if ask is None:
            return
        self._by_rhash.pop(ask.rhash, None)
        if ask.request is not None:
            self._late[ask.rhash] = ask.request
            while len(self._late) > 256:
                self._late.popitem(last=False)
        self.asks.close(ask, label or outcome.status, outcome, now=float(self.clock()))
        self.note("outcome", status=outcome.status, mode=outcome.mode, reasons=list(outcome.reasons)[:3])

    def _publish_evidence(self, evidence) -> None:
        for kind, ev in evidence:
            self.publish(make_incident(self.identity, kind, ev, clock=self.clock))


__all__ = ["UserClient", "Outcome", "OUTCOME_ACCEPTED", "OUTCOME_INTERCEPTED", "OUTCOME_REFUSED"]
