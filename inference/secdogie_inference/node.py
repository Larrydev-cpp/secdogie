"""A mesh node: serving (P2P+S), forwarding (P2P), answering (M/API) -- any mix.

**Serving -- the decision layer.** A serving node takes a user's signed
request, builds a ``View`` from its signed link state (excluding latched,
quarantined, locally suspect and dead nodes, and model nodes whose breaker is
open or whose lease is not current), asks its decision policy for legs, and
*checks the legs before signing them*: against the request (quorum, models,
pins) and against its own view (no excluded hop, no ineligible model node). A
policy that proposes a forbidden route never gets its decision signed; the
node reports its own decision layer (``decider.self-reported``) and the mesh
goes to lockdown -- state 3 of the diagram, "the decision model mutinies".

It then sends each leg, collects the answers, and puts them through the safety
layer: accepted answers go to the user, evidence becomes a signed incident,
disagreement and missing answers are retried on other paths (avoiding the legs
that failed, falling back to the coordinator) up to ``MAX_ATTEMPTS`` and then
refused. Timed-out legs feed ``Suspicion``; a node that keeps sitting on
failing legs is avoided and reported to the coordinator -- state 2.

**Forwarding.** Every node carries legs (``Carrier``). In lockdown a
forwarding-only node is the "new P2P" of state 3: it takes a request the user
pinned to two model nodes, links to them directly, signs a lockdown decision
that is exactly the pins, and hands every answer back to the user unjudged --
it makes no choice that could be wrong. Serving nodes refuse requests in
lockdown; the user goes to its standby forwarders instead.

**Answering -- M/API.** A model node opens the broker's sealed grants with its
transport key, checks each request and decision itself (a decision that works
against the request is ``decider.tampering`` evidence, reported and not
served), calls its backend with the leased secret, and guards the output before
signing: lease material in it, or a forbidden proposal for an action-bearing
request, means the *upstream model* misbehaved, so the node refuses and reports
``model.self-reported`` instead of signing something that would convict
itself. It answers a repeated request with the same output, so a retry over
another path is never equivocation.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from nacl.public import PrivateKey

from . import screen
from .breaker import CircuitBreaker
from .health import Suspicion
from .lease import LeaseSecret, check_lease, open_grant
from .mutiny import (
    DECIDER_SELF_REPORTED,
    DECIDER_TAMPERING,
    MODEL_SELF_REPORTED,
    decider_target,
    make_incident,
    model_target,
    node_target,
)
from .participant import Carrier, Config
from .protocol import (
    LOCKDOWN_QUORUM,
    MAX_ATTEMPTS,
    MAX_LEASES,
    MAX_OUTPUT,
    MODE_LOCKDOWN,
    ROLE_COORDINATOR,
    ROLE_FORWARD,
    ROLE_MODEL,
    ROLE_SERVE,
    STATUS_OK,
    STATUS_REFUSED,
    check_request,
    decision_shape,
    decision_violations,
    digest,
    make_decision,
    make_fault,
    make_response,
    routed_through,
)
from .router import Leg, NoRoute, Router, View
from .safety import ACCEPTED, INTERCEPTED, SafetyLayer
from .session import (
    DECIDING,
    DISPATCHED,
    OUTCOME_ABORTED,
    OUTCOME_ACCEPTED,
    OUTCOME_EXPIRED,
    OUTCOME_INTERCEPTED,
    OUTCOME_REFUSED,
    OUTCOME_RELAYED,
    SETTLING,
    BoundedCache,
    Session,
    SessionTable,
)

Backend = Callable[[str, str, str], str]  # (model, input, leased secret) -> output


SESSION_SLACK = 5.0  # beyond the legs' own timeouts, before a request's session expires


@dataclass(eq=False)
class _Pending(Session):
    """One request this node is routing: a session (``session.py``) keyed by
    the request's digest and owned by the user who submitted it."""

    request: dict | None = None
    quorum: int = 1
    lockdown: bool = False
    attempt: int = 0
    decision: dict | None = None
    legs: list[Leg] = field(default_factory=list)
    open: set[int] = field(default_factory=set)
    deadline: float = 0.0                              # the current attempt's legs time out
    avoid: set[str] = field(default_factory=set)
    decisions: set[str] = field(default_factory=set)   # digests of every attempt's decision
    items: list[tuple[dict, dict]] = field(default_factory=list)
    charged: set[str] = field(default_factory=set)     # providers already counted against their breaker

    @property
    def rhash(self) -> str:
        return self.key

    @property
    def user(self) -> str:
        return self.owner

    def release(self) -> None:
        self.request = self.decision = None
        self.legs, self.items = [], []
        self.open, self.avoid, self.decisions, self.charged = set(), set(), set(), set()


class MeshNode(Carrier):
    def __init__(self, identity, wire, trust, *, roles, backend: Backend | None = None,
                 transport_key: PrivateKey | None = None, policy=None, config: Config | None = None,
                 clock=time.time):
        super().__init__(identity, wire, trust, roles=roles, config=config, clock=clock)
        if self.roles & {ROLE_COORDINATOR} or not self.roles:
            raise ValueError("a mesh node serves, forwards and/or answers; the coordinator is its own class")
        if ROLE_SERVE in self.roles and not trust.deciders.contains(self.did):
            raise ValueError("a serving node must be on the deciders list")
        if ROLE_MODEL in self.roles and (backend is None or transport_key is None):
            raise ValueError("a model node needs a backend and its X25519 transport key")
        self.backend = backend
        self.policy = policy or Router()
        self._transport_key = transport_key
        cfg = self.config
        self.safety = SafetyLayer(trust, self.leases, self.ledger)
        self.suspicion = Suspicion(threshold=cfg.suspect_threshold, cooldown=cfg.suspect_cooldown)
        self.breaker = CircuitBreaker(threshold=cfg.breaker_threshold, cooldown=cfg.breaker_cooldown)
        self._held: dict[str, LeaseSecret] = {}                         # lease id -> opened lease
        # (request digest, model) -> (status, output, reason): a repeat gets the same answer
        self._served = BoundedCache(max_items=4096, max_bytes=cfg.answer_bytes,
                                    size=lambda v: 64 + len(v[1]) + len(v[2]))
        # the requests being routed, and the final frames of those already settled
        self.sessions: SessionTable[_Pending] = SessionTable(
            capacity=cfg.max_sessions, per_owner=cfg.max_sessions_per_user,
            retain_count=cfg.retain_results, retain_bytes=cfg.retain_bytes)
        self._dispatch.update({"submit": self._on_submit, "grant": self._on_grant})

    # -- serving: take a request -----------------------------------------------------

    def _on_submit(self, src: str, f: dict) -> None:
        now = float(self.clock())
        request = f.get("request")
        reason = check_request(request, self.trust, now=now)
        if reason or request["user"] != src:
            self.send(src, "refused", request_hash="", reason=f"bad request: {reason or 'not submitted by its user'}")
            return
        rhash = digest(request)
        self.users.add(src)
        final = self.sessions.final(rhash)
        if final is not None:
            kind, fields = final
            self.send(src, kind, **fields)
            return
        if self.sessions.live(rhash) is not None:
            return  # the same request over a second path
        lockdown = self.ledger.lockdown
        if lockdown and (ROLE_FORWARD not in self.roles or ROLE_SERVE in self.roles):
            why = "lockdown: the decision layer is suspended; use a standby forwarder"
        elif not lockdown and ROLE_SERVE not in self.roles:
            why = "not a serving node"
        elif self.ledger.excludes_node(self.did):
            why = "this node is latched"
        else:
            why = None
        if why:
            self._finish(src, rhash, "refused", reason=why)
            return
        quorum = max(request["quorum"], LOCKDOWN_QUORUM) if lockdown else request["quorum"]
        life = min(float(request["expires_at"]), now + self.config.leg_timeout * MAX_ATTEMPTS + SESSION_SLACK)
        p = _Pending(rhash, src, now, life, request=request, quorum=quorum, lockdown=lockdown)
        if not self.sessions.admit(p):
            # Nothing is kept for a request that did not fit: the user may try again later.
            mine = self.sessions.owned(src) >= self.sessions.per_owner
            why = (f"busy: {self.sessions.per_owner} of your requests are in flight here" if mine
                   else f"busy: {len(self.sessions)} requests in flight here")
            self.note("busy", request=rhash[:12], user=src[-8:])
            self.send(src, "refused", request_hash=rhash, reason=why)
            return
        self._decide(p, now)

    def _decide(self, p: _Pending, now: float) -> None:
        self.sessions.advance(p, DECIDING)
        if p.lockdown:
            legs, why = self._lockdown_legs(p.request, now)
        else:
            try:
                legs, why = self.policy.plan(self._view(p.request, now), p.request, quorum=p.quorum,
                                             avoid=frozenset(p.avoid)), None
            except NoRoute as exc:
                legs, why = None, f"no route: {exc}"
        if legs is None:
            self._settle_refused(p, why)
            return
        problems = self._self_check(p, legs, now)
        if problems:
            self._decision_mutiny(p, legs, problems)
            return
        mode = MODE_LOCKDOWN if p.lockdown else self.mode(now)
        decision = make_decision(self.identity, p.request, legs, mode=mode, epoch=self.epoch(),
                                 attempt=p.attempt, clock=self.clock)
        p.decision, p.legs = decision, list(legs)
        p.decisions.add(digest(decision))
        p.open = set(range(len(legs)))
        p.deadline = now + self.config.leg_timeout
        self.sessions.advance(p, DISPATCHED)
        self.note("decide", request=p.rhash[:12], attempt=p.attempt, mode=mode,
                  legs=[(leg.provider[-8:], leg.via, len(leg.path)) for leg in legs])
        for i, leg in enumerate(legs):
            if self.sessions.live(p.rhash) is not p or p.decision is not decision:
                return  # settled or re-decided while sending
            if not self.send(leg.path[1], "req", request=p.request, decision=decision, leg=i, hop=1):
                self.leg_failed(decision, i, leg.path[1], "the first hop is unreachable")

    def _lockdown_legs(self, request: dict, now: float):
        pins = request["pins"]
        if len(pins) < LOCKDOWN_QUORUM:
            return None, f"lockdown: pin at least {LOCKDOWN_QUORUM} model nodes"
        for provider, model in pins:
            why = self._ineligible(provider, model, now)
            if why:
                return None, f"pinned model node not eligible ({why}): {provider}"
        for provider, _model in pins:
            if provider not in self.neighbors:
                self.connect(provider)  # the new P2P links straight to each pinned model node
        return [Leg(provider, model, (self.did, provider)) for provider, model in pins], None

    def _ineligible(self, provider: str, model: str, now: float) -> str | None:
        if self.ledger.excludes_node(provider) or self.ledger.is_latched(model_target(model)):
            return "latched"
        if not any(p == provider and m == model for p, m, _ in self.leases.eligible([model], now)):
            return "no current lease"
        return None

    def _view(self, request: dict, now: float) -> View:
        excluded = self.excluded(now)
        adjacency = self.lsdb.adjacency(now, exclude=excluded)
        candidates = []
        for provider, model, _lease in self.leases.eligible(request["models"], now):
            if provider in adjacency and provider != self.did and provider not in excluded \
                    and not self.ledger.is_latched(model_target(model)) \
                    and self.breaker.available(node_target(provider), now) \
                    and self.breaker.available(model_target(model), now):
                candidates.append((provider, model))
        providers = {p for p, _ in candidates}
        central = {c: frozenset(adjacency.get(c, set()) & providers)
                   for c in adjacency.get(self.did, set()) if self.trust.coordinators.contains(c)}
        load = {d: st.load for d in adjacency if (st := self.lsdb.get(d)) is not None}
        return View(self.did, self.mode(now), adjacency, self.lsdb.transit(now, exclude=excluded),
                    candidates, central, load)

    def _self_check(self, p: _Pending, legs, now: float) -> list[str]:
        """Everything the signed decision must satisfy, checked before signing:
        the protocol's shape, the request, and this node's own exclusions."""
        mode = MODE_LOCKDOWN if p.lockdown else self.mode(now)
        body = {"decider": self.did, "mode": mode, "request_hash": p.rhash, "epoch": self.epoch(),
                "attempt": p.attempt, "issued_at": now, "legs": [leg.to_wire() for leg in legs]}
        shape = decision_shape(body, self.trust)
        if shape:
            return [shape]
        problems = decision_violations(body, p.request)
        if routed_through(body, self.excluded(now) | p.avoid):
            problems.append("excluded-hop")
        if any(self._ineligible(leg.provider, leg.model, now) for leg in legs):
            problems.append("ineligible-provider")
        return problems

    def _decision_mutiny(self, p: _Pending, legs, problems: list[str]) -> None:
        proposal = {"request_hash": p.rhash, "legs": [leg.to_wire() for leg in legs]}
        evidence = {"reporter": self.did, "proposal": proposal, "violation": ", ".join(problems)[:200]}
        self.note("self-report", target=decider_target(self.did), problems=problems)
        self._settle_refused(p, "lockdown: this node's decision layer proposed a forbidden route")
        self.publish(make_incident(self.identity, DECIDER_SELF_REPORTED, evidence, clock=self.clock))

    # -- serving: collect and settle ---------------------------------------------------

    def collect(self, decision: dict, leg_i: int, response: dict) -> None:
        p = self.sessions.live(decision["request_hash"])  # a closed session takes nothing more
        dh = digest(decision)
        if p is None or dh not in p.decisions:
            return
        p.items.append((decision, response))
        if p.decision is not None and dh == digest(p.decision):
            self.suspicion.delivered(p.legs[leg_i].interior)
            p.open.discard(leg_i)
            if not p.open:
                self._settle(p)

    def leg_failed(self, decision: dict, leg_i: int, unreachable: str, reason: str) -> None:
        p = self.sessions.live(decision["request_hash"])
        if p is None or p.decision is None or digest(decision) != digest(p.decision) or leg_i not in p.open:
            return
        self.note("leg-failed", request=p.rhash[:12], leg=leg_i, unreachable=unreachable[-8:], reason=reason)
        p.avoid.add(unreachable)
        p.open.discard(leg_i)
        if not p.open:
            self._settle(p)

    def on_tick(self, now: float) -> None:
        for p in self.sessions.sessions():
            if self.sessions.live(p.rhash) is not p or p.state != DISPATCHED or now < p.deadline:
                continue
            for i in sorted(p.open):
                leg = p.legs[i]
                interior = leg.interior
                for d in self.suspicion.timed_out(interior, now):
                    self.note("suspect", node=d[-8:])
                    self._report_fault(d, "timeout", p.rhash)
                if not interior:
                    self.breaker.failure(node_target(leg.provider), now)
                p.avoid |= set(interior) or {leg.provider}
            p.open.clear()
            self._settle(p)
        for p in self.sessions.expired(now):
            if self.sessions.live(p.rhash) is p:
                self._settle_refused(p, "expired: the request ran out of time here", OUTCOME_EXPIRED)
        for lid in [lid for lid, s in self._held.items() if s.lease["expires_at"] <= now]:
            self._held.pop(lid).wipe()

    def _settle(self, p: _Pending) -> None:
        now = float(self.clock())
        self.sessions.advance(p, SETTLING)
        if p.lockdown:
            # The new P2P judges nothing; it still reports what it can prove.
            verdict = self.safety.adjudicate(p.request, p.items, now=now, quorum=p.quorum)
            items = [[d, r] for d, r in p.items]
            self._close(p, OUTCOME_RELAYED, "result", request_hash=p.rhash, items=items)
            self._publish_evidence(verdict.evidence)
            return
        if self.ledger.lockdown:
            self._settle_refused(p, "lockdown: the decision layer is suspended; use a standby forwarder",
                                 OUTCOME_ABORTED)
            return
        verdict = self.safety.adjudicate(p.request, p.items, now=now, quorum=p.quorum,
                                         advisory=self.advisory_at(p.decision["epoch"]) if p.decision else None)
        for provider in sorted({who for who, _why in verdict.rejected} - p.charged):
            p.charged.add(provider)  # once per request: retries re-judge the same answers
            self.breaker.failure(node_target(provider), now)
        if verdict.status == ACCEPTED:
            for provider in verdict.providers:
                self.breaker.success(node_target(provider))
            self.note("accepted", request=p.rhash[:12], providers=[x[-8:] for x in verdict.providers])
            items = [[d, r] for d, r in p.items]
            self._close(p, OUTCOME_ACCEPTED, "result", request_hash=p.rhash, items=items)
            return
        if verdict.evidence:
            # The verdict reaches the user before the incident that locks the mesh down.
            self._settle_refused(p, f"intercepted: {'; '.join(verdict.reasons)[:180]}", OUTCOME_INTERCEPTED)
            self._publish_evidence(verdict.evidence)
            return
        if verdict.status == INTERCEPTED:  # valid answers that disagree
            rhash = p.rhash
            self._settle_refused(p, "intercepted: the model nodes disagree", OUTCOME_INTERCEPTED)
            for provider in verdict.dissent:
                self.breaker.failure(node_target(provider), now)
                self._report_fault(provider, "divergent", rhash)
            return
        if p.attempt + 1 < MAX_ATTEMPTS:
            p.attempt += 1
            self._decide(p, now)
            return
        self._settle_refused(p, f"no quorum: {'; '.join(verdict.reasons)[:180]}")

    def _settle_refused(self, p: _Pending, reason: str, outcome: str = OUTCOME_REFUSED) -> None:
        self._close(p, outcome, "refused", reason=reason)

    def _close(self, p: _Pending, outcome: str, kind: str, **fields) -> None:
        """End a request's session -- the only way one ends -- and tell its user.
        Build ``fields`` before calling: closing releases what the session held."""
        user, rhash = p.user, p.rhash
        self.sessions.close(p, outcome)
        self._finish(user, rhash, kind, **fields)

    def _finish(self, user: str, rhash: str, kind: str, **fields) -> None:
        """Send the final frame for ``rhash`` and keep it (bounded) for repeats."""
        if kind == "refused":
            fields = {"request_hash": rhash, **fields}
            self.note("refused", request=rhash[:12], reason=fields["reason"])
        self.sessions.remember(rhash, (kind, fields), now=float(self.clock()))
        self.send(user, kind, **fields)

    def _publish_evidence(self, evidence) -> None:
        for kind, ev in evidence:
            self.publish(make_incident(self.identity, kind, ev, clock=self.clock))

    def _report_fault(self, accused: str, reason: str, rhash: str = "") -> None:
        self.publish(make_fault(self.identity, accused, reason, request_hash=rhash, clock=self.clock))

    def on_incident(self, incident) -> None:
        if not self.ledger.lockdown:
            return
        for p in self.sessions.sessions():
            if not p.lockdown and self.sessions.live(p.rhash) is p:
                self._settle_refused(p, "lockdown: the decision layer is suspended; use a standby forwarder",
                                     OUTCOME_ABORTED)

    def on_advisory(self, advisory: dict) -> None:
        """Act on the repairs that name this node: open the suggested links."""
        down = set(advisory["failed"]) | set(advisory["quarantined"])
        for a, b in advisory["bypass"] + advisory["recruit"]:
            other = b if a == self.did else a if b == self.did else None
            if other and other not in self.neighbors and other not in down \
                    and not self.ledger.excludes_node(other) and self.connect(other):
                self.note("repair-link", peer=other[-8:])

    def local_exclusions(self, now: float) -> set[str]:
        return self.suspicion.suspects(now)

    def breaker_state(self, did: str, now: float) -> str:
        return self.breaker.state(node_target(did), now) if ROLE_MODEL in self.lsdb.roles(did) else ""

    def load(self) -> float:
        return min(len(self.sessions) / self.sessions.capacity, 1.0)

    # -- answering: M/API ---------------------------------------------------------------

    def _on_grant(self, src: str, f: dict) -> None:
        if ROLE_MODEL not in self.roles or not self.trust.brokers.contains(src):
            return
        lease, sealed = f.get("lease"), f.get("sealed")
        if check_lease(lease, self.trust) or lease["subject"] != self.did or lease["broker"] != src:
            return
        if not isinstance(sealed, str) or self.leases.is_revoked(lease["lease_id"]):
            return
        opened = open_grant(lease, sealed, self._transport_key)
        if opened is None:
            self.note("grant-rejected", lease=lease["lease_id"])
            return
        self._held[opened.lease_id] = opened
        self.leases.add(lease)
        self.note("grant", model=opened.model, version=lease["key_version"])

    def on_lease_revoked(self, lease_ids: frozenset) -> None:
        for lid in lease_ids:
            held = self._held.pop(lid, None)
            if held is not None:
                held.wipe()
                self.note("lease-revoked", lease=lid)

    def advertised_leases(self, now: float) -> list[dict]:
        live = [s.lease for s in self._held.values()
                if s.lease["valid_from"] <= now < s.lease["expires_at"]
                and not self.leases.is_revoked(s.lease_id)]
        live.sort(key=lambda x: (-x["key_version"], -x["expires_at"]))
        return live[:MAX_LEASES]

    def _secret_for(self, model: str, now: float) -> LeaseSecret | None:
        live = [s for s in self._held.values() if s.model == model
                and self.leases.check(s.lease, provider=self.did, model=model, now=now) is None]
        return max(live, key=lambda s: (s.lease["key_version"], s.lease["expires_at"]), default=None)

    def serve(self, request: dict, decision: dict, leg_i: int, now: float) -> None:
        leg = decision["legs"][leg_i]
        if ROLE_MODEL not in self.roles or leg["provider"] != self.did:
            return
        hop = len(leg["path"]) - 1
        violations = decision_violations(decision, request)
        advisory = self.advisory_at(decision["epoch"])
        evidence = {"request": request, "decision": decision}
        if advisory is not None and routed_through(decision, advisory["quarantined"]):
            violations.append("quarantined-hop")
            evidence["advisory"] = advisory
        if violations:
            self.publish(make_incident(self.identity, DECIDER_TAMPERING, evidence, clock=self.clock))
            self.nack(decision, leg_i, hop, unreachable=self.did, reason="decision tampering")
            return
        model = leg["model"]
        why = self.may_carry(decision, now)
        if why is None and (self.ledger.excludes_node(self.did) or self.ledger.is_latched(model_target(model))):
            why = "the node or model is latched"
        held = None if why else self._secret_for(model, now)
        if why or held is None:
            self.nack(decision, leg_i, hop, unreachable=self.did, reason=why or "no current lease")
            return
        rhash = digest(request)
        key = (rhash, model)
        answer = self._served.get(key)
        if answer is None:
            answer = self._answer(request, model, held)
            self._served[key] = answer
        status, output, reason = answer
        response = make_response(self.identity, request, decision, model, held.lease, output=output,
                                 status=status, reason=reason, clock=self.clock)
        self.send(leg["path"][hop - 1], "resp", decision=decision, response=response, leg=leg_i, hop=hop - 1)

    def _answer(self, request: dict, model: str, held: LeaseSecret) -> tuple[str, str, str]:
        try:
            output = self.backend(model, request["input"], held.secret)
        except Exception as exc:  # noqa: BLE001 - a backend failure is a refusal, not a crash
            return STATUS_REFUSED, "", f"backend error: {type(exc).__name__}"
        if not isinstance(output, str):
            return STATUS_REFUSED, "", "backend returned no text"
        if len(output) > MAX_OUTPUT:
            return STATUS_REFUSED, "", "answer too long for one frame"
        problem = self.guard_output(output, held, request)
        if problem:
            evidence = {"reporter": self.did, "lease": held.lease, "violation": problem}
            self.note("self-report", target=model_target(model), problem=problem)
            self.publish(make_incident(self.identity, MODEL_SELF_REPORTED, evidence, clock=self.clock))
            return STATUS_REFUSED, "", problem
        return STATUS_OK, output, ""

    def guard_output(self, output: str, held: LeaseSecret, request: dict) -> str | None:
        """What the upstream model did that this node will not sign."""
        if held.secret in output:
            return "the upstream answer echoed the leased key"
        if held.canary in output:
            return "the upstream answer echoed the lease canary"
        if request["scopes"]:
            bad = screen.forbidden(output)
            if bad:
                return f"the upstream answer proposed a forbidden capability: {', '.join(bad)}"[:200]
        return None


__all__ = ["MeshNode", "Backend"]
