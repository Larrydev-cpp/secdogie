"""The user's end: sign the request, judge the answers yourself.

``UserClient`` trusts no node with the verdict. Every answer that comes back
goes through its own ``SafetyLayer`` -- signatures, leases (learned from the
answers themselves and from directories, always broker-verified), the user's
quorum, the deterministic screens -- so a serving node that lies about what the
model said, or a forwarder that swaps an answer, achieves nothing but a refusal.

**Normal and degraded.** The request goes to an entry -- a serving node the user
is attached to -- or to ``fanout`` of them at once (the diagram's user is linked
to two). A refusal or a timeout moves on to the next entry.

**Lockdown.** Once the client holds a verified incident it stops using serving
nodes: the decision layer is suspended. It picks a standby forwarder, fetches a
directory if it lacks leases, **pins** the providers itself -- at least two,
distinct nodes, distinct models first, deterministic order -- signs a request
with quorum two, and accepts only answers that agree. A pinned node that does
not answer is dropped from the next try.

**Sealed input.** By default the prompt is sealed (``seal.py``) to the model
nodes that may answer -- every node with a current lease for one of the
requested models, up to ``MAX_RECIPIENTS``; in lockdown, exactly the pins. The
keys come from those nodes' broker-signed leases. With no node to seal to yet,
the client fetches a directory and waits; it never falls back to sending the
prompt in the clear. ``seal=False`` sends it in the clear on purpose.

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
    MAX_INPUT,
    MAX_RECIPIENTS,
    MAX_SEALED_INPUT,
    MODE_LOCKDOWN,
    MODE_NORMAL,
    digest,
    is_x25519,
    make_request,
)
from .safety import ACCEPTED, INTERCEPTED, SafetyLayer, default_agree

HELLO_EVERY = 30.0  # re-attach to entries and standby, refreshing leases and incidents

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


@dataclass
class _Ask:
    models: tuple[str, ...]
    text: str
    scopes: tuple[str, ...]
    quorum: int
    ttl: float
    request: dict | None = None
    rhash: str = ""
    mode: str = MODE_NORMAL
    tries: int = 0
    deadline: float = 0.0
    tried: list[str] = field(default_factory=list)
    awaiting: set[str] = field(default_factory=set)   # nodes the current request went to, yet to refuse
    unresponsive: set[str] = field(default_factory=set)
    waiting_dir: bool = False
    last_reason: str = ""
    outcome: Outcome | None = None


class UserClient(Participant):
    beats = False

    def __init__(self, identity, wire, trust, *, entries, standby=(), fanout: int = 1, timeout: float = 15.0,
                 max_tries: int = 4, agree: Callable[[str, str], bool] = default_agree,
                 config: Config | None = None, clock=time.time, ledger_store=None, seal: bool = True):
        super().__init__(identity, wire, trust, config=config, clock=clock, ledger_store=ledger_store)
        if not trust.users.contains(self.did):
            raise ValueError("a user client must be on the users list")
        self.seal = seal
        self.entries = list(entries)
        self.standby = list(standby)
        self.fanout = max(1, int(fanout))
        self.timeout = float(timeout)
        self.max_tries = int(max_tries)
        self.safety = SafetyLayer(trust, self.leases, self.ledger, agree=agree)
        self._asks: dict[str, _Ask] = {}
        self._by_rhash: dict[str, str] = {}
        self._late: OrderedDict[str, dict] = OrderedDict()  # rhash -> request, for asks already decided
        self._next_hello = 0.0
        for did in self.entries + self.standby:
            self.connect(did)
        self._dispatch.update({"result": self._on_result, "refused": self._on_refused})

    # -- the API ----------------------------------------------------------------------

    def ask(self, models, text: str, *, scopes=(), quorum: int = 1, ttl: float = 60.0) -> str:
        """Start a request; returns a handle for ``outcome``. Input that cannot
        travel -- too long, or too many bytes to seal into one datagram -- is
        refused here, before anything is sent: signing may happen later, once a
        directory arrives, where a raise would land in a frame handler."""
        if not isinstance(text, str) or len(text) > MAX_INPUT:
            raise ValueError("input missing or too long")
        if self.seal and len(text.encode("utf-8")) > MAX_SEALED_INPUT:
            raise ValueError(f"input too long to seal into one datagram: {len(text.encode('utf-8'))} bytes, "
                             f"at most {MAX_SEALED_INPUT}")
        with self._lock:
            handle = secrets.token_hex(8)
            self._asks[handle] = _Ask(tuple(models), text, tuple(scopes), int(quorum), float(ttl))
            self._submit(handle, float(self.clock()))
            return handle

    def outcome(self, handle: str) -> Outcome | None:
        with self._lock:
            ask = self._asks.get(handle)
            return ask.outcome if ask is not None else None

    # -- submitting -------------------------------------------------------------------

    def _submit(self, handle: str, now: float) -> None:
        ask = self._asks[handle]
        if ask.outcome is not None:
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
        seal_to = None
        if self.seal:
            seal_to = self.seal_targets(ask.models, now)
            if not seal_to:
                self._await_directory(handle, ask, now, fresh[:self.fanout])
                return
            ask.waiting_dir = False
        request = self._sign(handle, ask, quorum=ask.quorum, seal_to=seal_to)
        ask.awaiting = set(fresh[:self.fanout])
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
            self.send(forwarder, "dir?")
            return
        ask.waiting_dir = False
        seal_to = None
        if self.seal:
            keys = self._seal_keys(ask.models, now)
            seal_to = [(p, keys[p]) for p, _m in pins]  # exactly the pins: pick_pins chose sealable ones
        request = self._sign(handle, ask, quorum=quorum, pins=pins, seal_to=seal_to)
        ask.tried.append(forwarder)
        ask.awaiting = {forwarder}
        for incident in self.ledger.signed_incidents()[-2:]:
            self.send(forwarder, "flood", obj=incident)  # the proof travels ahead of the request
        self.send(forwarder, "submit", request=request)
        self.note("lockdown-submit", via=forwarder[-8:], pins=[p[-8:] for p, _ in pins])

    def pick_pins(self, models, quorum: int, now: float, *, avoid=frozenset()) -> list[tuple[str, str]]:
        """Distinct model nodes holding a current lease, not latched, distinct
        models first; deterministic so the choice is auditable."""
        order = {m: i for i, m in enumerate(sorted(set(models)))}
        sealable = self._seal_keys(models, now) if self.seal else None
        cands = sorted(((order[m], p, m) for p, m, _ in self.leases.eligible(models, now)
                        if p not in avoid and not self.ledger.excludes_node(p)
                        and not self.ledger.is_latched(model_target(m))
                        and (sealable is None or p in sealable)))
        pins: list[tuple[str, str]] = []
        for distinct in (True, False):
            for _i, p, m in cands:
                if len(pins) >= quorum:
                    return pins
                if any(p == q for q, _ in pins) or (distinct and any(m == n for _, n in pins)):
                    continue
                pins.append((p, m))
        return pins

    # -- sealing ----------------------------------------------------------------------

    def _seal_keys(self, models, now: float) -> dict[str, str]:
        """Model node -> the X25519 key its newest current lease names, for the
        nodes that may answer ``models``: not latched, the model not latched."""
        keys: dict[str, str] = {}
        for p, m, lease in self.leases.eligible(models, now):  # newest key version first
            if (p not in keys and is_x25519(lease.get("seal_key")) and not self.ledger.excludes_node(p)
                    and not self.ledger.is_latched(model_target(m))):
                keys[p] = lease["seal_key"]
        return keys

    def seal_targets(self, models, now: float) -> list[tuple[str, str]]:
        """Whom a normal request is sealed to: every model node that may answer,
        up to ``MAX_RECIPIENTS``, distinct models first, so the decider has a
        choice of routes and of providers for a quorum."""
        keys = self._seal_keys(models, now)
        per_model: dict[str, list[str]] = {m: [] for m in sorted(set(models))}
        for p, m, _ in sorted(self.leases.eligible(models, now), key=lambda x: (x[1], x[0])):
            if p in keys and p not in per_model[m]:
                per_model[m].append(p)
        chosen: list[str] = []
        while len(chosen) < MAX_RECIPIENTS and any(per_model.values()):
            for queue in per_model.values():  # one node per model per round: every vendor gets a seat
                if queue and len(chosen) < MAX_RECIPIENTS:
                    p = queue.pop(0)
                    if p not in chosen:
                        chosen.append(p)
        return [(p, keys[p]) for p in chosen]

    def _await_directory(self, handle: str, ask: _Ask, now: float, entries) -> None:
        """No model node to seal to yet: fetch a directory and try once more.
        A second miss is a refusal -- never the prompt in the clear."""
        if ask.waiting_dir:
            self._finish(handle, Outcome(OUTCOME_REFUSED, mode=ask.mode,
                                         reasons=("no model node to seal the input to",)))
            return
        ask.waiting_dir = True
        ask.awaiting = set()  # a late refusal of an earlier try must not end this wait
        ask.deadline = now + self.timeout
        for entry in entries:
            self.send(entry, "dir?")

    # -- answers ------------------------------------------------------------------------

    def _on_result(self, src: str, f: dict) -> None:
        now = float(self.clock())
        rhash = f.get("request_hash")
        items = [tuple(x) for x in (f.get("items") or []) if isinstance(x, list) and len(x) == 2
                 and isinstance(x[0], dict) and isinstance(x[1], dict)]
        for _d, r in items:
            self.leases.add(r.get("lease"))
        handle = self._by_rhash.get(rhash)
        ask = self._asks.get(handle) if handle else None
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
        ask = self._asks.get(handle) if handle else None
        if ask is None or ask.outcome is not None or src not in ask.awaiting:
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
        self._submit(handle, float(self.clock()))

    def on_directory(self, src: str) -> None:
        now = float(self.clock())
        for handle, ask in list(self._asks.items()):
            if ask.waiting_dir and ask.outcome is None:
                if ask.mode == MODE_LOCKDOWN:
                    self._submit_lockdown(handle, ask, now)
                else:
                    self._submit_normal(handle, ask, now)

    def on_incident(self, incident) -> None:
        now = float(self.clock())
        for handle, ask in list(self._asks.items()):
            if ask.outcome is None and ask.mode != MODE_LOCKDOWN:
                self._submit(handle, now)

    def on_tick(self, now: float) -> None:
        if now >= self._next_hello:
            self._next_hello = now + HELLO_EVERY
            for did in self.entries + self.standby:
                self.send(did, "dir?")  # attach, so incidents reach us before we ask anything
        for handle, ask in list(self._asks.items()):
            if ask.outcome is None and now >= ask.deadline:
                if ask.mode == MODE_LOCKDOWN and ask.tried:
                    ask.unresponsive.add(ask.tried[-1])  # the forwarder itself went quiet
                    ask.waiting_dir = False
                self._submit(handle, now)

    def _finish(self, handle: str, outcome: Outcome) -> None:
        ask = self._asks[handle]
        ask.outcome = outcome
        self._by_rhash.pop(ask.rhash, None)
        if ask.request is not None:
            self._late[ask.rhash] = ask.request
            while len(self._late) > 256:
                self._late.popitem(last=False)
        self.note("outcome", status=outcome.status, mode=outcome.mode, reasons=list(outcome.reasons)[:3])

    def _publish_evidence(self, evidence) -> None:
        for kind, ev in evidence:
            self.publish(make_incident(self.identity, kind, ev, clock=self.clock))


__all__ = ["UserClient", "Outcome", "OUTCOME_ACCEPTED", "OUTCOME_INTERCEPTED", "OUTCOME_REFUSED"]
