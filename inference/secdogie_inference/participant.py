"""What every member of the inference mesh shares.

A ``Participant`` -- serving node, forwarder, model node, coordinator, broker,
user -- holds the same four things, fed by the same flooded objects:

  * ``lsdb``       the signed link state (heartbeats), so it can route;
  * ``leases``     the broker-signed leases and lease revocations it has seen;
  * ``ledger``     the verified mutiny incidents and operator resets;
  * ``advisories`` the newest advisory from each coordinator.

Fault reports are flooded too, so they reach the coordinator from deciders
that are not linked to it; only the coordinator acts on them.

Flooding is plain: a newly verified object is passed to every neighbour except
the one it came from; anything already seen, or that does not verify, stops
there. Incidents, resets and lease revocations also go to attached users.

A ``Carrier`` additionally carries legs: a request walks its decision's path
toward the model node, the answer walks it back, and a hop that cannot go on
turns the leg back as a ``nack``. A carrier decides nothing -- it checks that a
frame is where the signed decision says it should be, and refuses to carry an
ordinary decision during lockdown.

Thread-safe: ``handle``, ``tick`` and ``publish`` run under one lock, so a UDP
receive thread and a timer thread can drive the same participant.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

from .lease import LEASE_REVOCATION, LeaseBook
from .mutiny import INCIDENT, RESET, MutinyLedger
from .protocol import (
    ADVISORY,
    FAULT,
    HEARTBEAT,
    MAX_REASON,
    MODE_LOCKDOWN,
    ROLES,
    check_advisory,
    check_decision,
    check_fault,
    check_request,
    check_response,
    digest,
    frame,
    is_did,
    is_int,
    make_heartbeat,
    parse_frame,
)
from .state import (
    CAUSE_ADVISORY,
    CAUSE_LATCH,
    CAUSE_LIVENESS,
    CAUSE_RESET,
    ModeInputs,
    ModeMachine,
    NodeHealth,
)
from .topology import LinkStateDB

log = logging.getLogger("secdogie_inference")

FLOODED = frozenset({HEARTBEAT, ADVISORY, FAULT, INCIDENT, RESET, LEASE_REVOCATION})
TO_USERS = frozenset({INCIDENT, RESET, LEASE_REVOCATION})
DIR_INCIDENTS = 2   # newest incidents a directory answer carries (each may hold a full answer)
DIR_RESETS = 8


@dataclass(frozen=True)
class Config:
    heartbeat_every: float = 2.0
    fail_after: float = 7.0         # no new heartbeat for this long: the node is down
    leg_timeout: float = 5.0
    advise_every: float = 5.0
    suspect_threshold: int = 2
    suspect_cooldown: float = 60.0
    breaker_threshold: int = 3
    breaker_cooldown: float = 30.0
    quarantine_threshold: int = 2   # distinct reporters
    quarantine_window: float = 60.0
    quarantine_cooldown: float = 60.0
    probation: float = 30.0
    lease_ttl: float = 600.0
    lease_renew_at: float = 0.5     # renew a lease with less than this fraction of its TTL left


class _Recent:
    """A bounded set of recently seen keys."""

    def __init__(self, size: int):
        self._keys: OrderedDict[str, None] = OrderedDict()
        self._size = size

    def add(self, key: str) -> bool:
        if key in self._keys:
            return False
        self._keys[key] = None
        while len(self._keys) > self._size:
            self._keys.popitem(last=False)
        return True


class Participant:
    roles: frozenset = frozenset()
    beats = True  # floods heartbeats (users do not)

    def __init__(self, identity, wire, trust, *, roles=None, config: Config | None = None, clock=time.time):
        self.identity = identity
        self.did = identity.did
        self.wire = wire
        self.trust = trust
        self.config = config or Config()
        self.clock = clock
        if roles is not None:
            self.roles = frozenset(roles)
        if not self.roles <= ROLES:
            raise ValueError(f"unknown role(s): {sorted(self.roles - ROLES)}")
        self.ledger = MutinyLedger(trust)
        self.leases = LeaseBook(trust)
        self.lsdb = LinkStateDB(trust, fail_after=self.config.fail_after)
        self.advisories: dict[str, dict] = {}
        self.neighbors: set[str] = set()
        self.users: set[str] = set()
        self.events: deque = deque(maxlen=1024)
        self._seen = _Recent(8192)
        self._seq = time.time_ns()  # keeps increasing across restarts
        self._next_beat = 0.0
        self.machine = ModeMachine()     # the mode, as an explicit state machine (state.py)
        self._causes: set[str] = set()   # what was absorbed since the mode was last evaluated
        self._lock = threading.RLock()
        self._dispatch = {"flood": self._on_flood, "dir?": self._on_dir_query, "dir": self._on_dir}

    # -- wiring -------------------------------------------------------------------

    def connect(self, did: str) -> bool:
        """Open a link to a mesh node or attach a user. Nobody else."""
        if did == self.did:
            return False
        if self.trust.mesh.contains(did):
            self.neighbors.add(did)
        elif self.trust.users.contains(did):
            self.users.add(did)
        else:
            return False
        self.wire.connect(did)
        return True

    def send(self, to: str, kind: str, **fields) -> bool:
        return self.wire.send(to, frame(kind, **fields))

    def note(self, event: str, **detail) -> None:
        self.events.append((self.clock(), event, detail))
        log.info("%s %s %s", self.did[-8:], event, detail)

    def noted(self, event: str) -> list[dict]:
        return [d for _t, e, d in self.events if e == event]

    # -- inbound ------------------------------------------------------------------

    def handle(self, from_did: str, payload: bytes) -> None:
        """Inbound frame from a transport-verified neighbour (a ChannelMux
        channel handler)."""
        with self._lock:
            f = parse_frame(payload)
            if f is None or not self.trust.peers.contains(from_did):
                return
            fn = self._dispatch.get(f["k"])
            if fn is None:
                return
            try:
                fn(from_did, f)
            except Exception:  # noqa: BLE001 - one bad frame must not take the node down
                log.exception("frame %s from %s failed", f["k"], from_did)
            self._check_mode()

    def tick(self) -> None:
        with self._lock:
            now = float(self.clock())
            if self.beats and now >= self._next_beat:
                self._beat(now)
                self._next_beat = now + self.config.heartbeat_every
            self.on_tick(now)
            self._check_mode()

    def publish(self, obj: dict) -> bool:
        """Apply a signed object here and flood it -- how an operator's reset or
        a locally found incident enters the mesh."""
        with self._lock:
            self._seen.add(digest(obj))
            ok = self.absorb(obj)
            if ok:
                self.flood(obj)
            self._check_mode()
            return ok

    # -- flooding -----------------------------------------------------------------

    def flood(self, obj: dict, *, exclude: str | None = None) -> None:
        targets = set(self.neighbors)
        if obj.get("type") in TO_USERS:
            targets |= self.users
        for did in sorted(targets):
            if did != exclude:
                self.send(did, "flood", obj=obj)

    def _on_flood(self, src: str, f: dict) -> None:
        obj = f.get("obj")
        if not isinstance(obj, dict) or obj.get("type") not in FLOODED:
            return
        if not self._seen.add(digest(obj)):
            return
        if self.absorb(obj, src=src):
            self.flood(obj, exclude=src)

    def absorb(self, obj: dict, *, src: str | None = None) -> bool:
        """Verify and apply one flooded object; True when it changed anything
        (and so should travel on)."""
        now = float(self.clock())
        kind = obj.get("type")
        if kind == HEARTBEAT:
            if not self.lsdb.merge(obj, now=now):
                return False
            node = obj["node"]
            for lease in obj["leases"]:
                if isinstance(lease, dict) and lease.get("subject") == node:
                    self.leases.add(lease)
            if src == node and node not in self.neighbors and not self.ledger.excludes_node(node):
                self.neighbors.add(node)  # a direct heartbeat is a link offer; links are mutual
            return True
        if kind == ADVISORY:
            if check_advisory(obj, self.trust, now=now) is not None:
                return False
            cur = self.advisories.get(obj["coordinator"])
            if cur is not None and obj["epoch"] <= cur["epoch"]:
                return False
            self.advisories[obj["coordinator"]] = obj
            self._causes.add(CAUSE_ADVISORY)
            self.on_advisory(obj)
            return True
        if kind == FAULT:
            if check_fault(obj, self.trust, now=now) is not None:
                return False
            self.on_fault(obj, now)
            return True
        if kind == INCIDENT:
            inc = self.ledger.add(obj)
            if inc is None:
                return False
            self.note("incident", kind=inc.kind, targets=sorted(inc.targets), id=inc.incident_id)
            self._causes.add(CAUSE_LATCH)
            self.on_incident(inc)
            return True
        if kind == RESET:
            new, unlatched = self.ledger.reset(obj)
            if new:
                self._causes.add(CAUSE_RESET)
            if unlatched:
                self.note("reset", targets=sorted(unlatched))
                self.on_reset(unlatched)
            return new
        if kind == LEASE_REVOCATION:
            newly = self.leases.revoke(obj)
            if newly:
                self.on_lease_revoked(newly)
            return bool(newly)
        return False

    # -- the directory: what a newcomer (a user entering lockdown) needs ---------------

    def _on_dir_query(self, src: str, f: dict) -> None:
        now = float(self.clock())
        if self.trust.users.contains(src):
            self.users.add(src)  # a user's directory query attaches it: incidents reach it from now on
        self.send(src, "dir", leases=self.leases.live(now)[:32],
                  incidents=self.ledger.signed_incidents()[-DIR_INCIDENTS:],
                  resets=self.ledger.signed_resets()[-DIR_RESETS:])

    def _on_dir(self, src: str, f: dict) -> None:
        if not self.trust.mesh.contains(src):
            return
        for lease in f.get("leases") or []:
            self.leases.add(lease)
        for obj in (f.get("incidents") or []) + (f.get("resets") or []):
            if isinstance(obj, dict) and self._seen.add(digest(obj)):
                self.absorb(obj)
        self.on_directory(src)

    # -- the view -----------------------------------------------------------------

    def mode_inputs(self, now: float | None = None) -> ModeInputs:
        """Everything the mode depends on, as this participant holds it now."""
        now = float(self.clock()) if now is None else now
        return ModeInputs(
            lockdown_latches=frozenset(self.ledger.latched()) if self.ledger.lockdown else frozenset(),
            failed=frozenset(d for adv in self.advisories.values() for d in adv["failed"]),
            quarantined=frozenset(self.quarantined()),
            stale=frozenset(d for d in self.lsdb.known() if d != self.did and not self.lsdb.alive(d, now)),
        )

    def mode(self, now: float | None = None) -> str:
        """normal | degraded | lockdown, derived from what this participant holds
        (``state.ModeMachine.derive``)."""
        return ModeMachine.derive(self.mode_inputs(now))

    def health_snapshot(self, now: float | None = None) -> dict[str, NodeHealth]:
        """This participant's view of every other node it knows, across the
        per-node machines: liveness, suspicion, quarantine, breaker, latch."""
        now = float(self.clock()) if now is None else now
        suspects = self.local_exclusions(now)
        out = {}
        for d in self.lsdb.known():
            if d == self.did:
                continue
            out[d] = NodeHealth(d, self.lsdb.roles(d), self.lsdb.alive(d, now), d in suspects,
                                self.quarantine_status(d, now), self.breaker_state(d, now),
                                self.ledger.excludes_node(d), False)
        return out

    def quarantine_status(self, did: str, now: float) -> str:
        return "quarantined" if did in self.quarantined() else "healthy"

    def breaker_state(self, did: str, now: float) -> str:
        return ""

    def epoch(self) -> int:
        return max((adv["epoch"] for adv in self.advisories.values()), default=0)

    def advisory_at(self, epoch: int) -> dict | None:
        return next((adv for adv in self.advisories.values() if adv["epoch"] == epoch), None)

    def quarantined(self) -> set[str]:
        return {d for adv in self.advisories.values() for d in adv["quarantined"]}

    def excluded(self, now: float) -> set[str]:
        """Nodes no route of ours may use: latched, quarantined, locally suspect."""
        out = {d for d in self.lsdb.known() if self.ledger.excludes_node(d)}
        return out | self.quarantined() | self.local_exclusions(now)

    def _check_mode(self) -> None:
        """Step the mode machine with what was absorbed since the last step.
        Time passing (liveness) is always a possible cause; leaving lockdown
        takes a reset, or ``state.IllegalTransition`` is raised."""
        now = float(self.clock())
        causes, self._causes = self._causes | {CAUSE_LIVENESS}, set()
        t = self.machine.step(self.mode_inputs(now), causes=causes, now=now)
        if t is not None:
            self.note("mode", mode=t.new, was=t.old, cause=t.cause)
            self.on_mode(t.old, t.new)

    def _beat(self, now: float) -> None:
        self._seq += 1
        hb = make_heartbeat(self.identity, roles=sorted(self.roles), seq=self._seq,
                            neighbors=sorted(self.neighbors), load=self.load(),
                            leases=self.advertised_leases(now), clock=self.clock)
        self.lsdb.merge(hb, now=now)
        self._seen.add(digest(hb))
        self.flood(hb)

    # -- hooks ----------------------------------------------------------------------

    def on_tick(self, now: float) -> None: ...
    def on_advisory(self, advisory: dict) -> None: ...
    def on_fault(self, report: dict, now: float) -> None: ...
    def on_incident(self, incident) -> None: ...
    def on_reset(self, unlatched: frozenset) -> None: ...
    def on_lease_revoked(self, lease_ids: frozenset) -> None: ...
    def on_directory(self, src: str) -> None: ...
    def on_mode(self, old: str | None, new: str) -> None: ...
    def local_exclusions(self, now: float) -> set[str]: return set()
    def load(self) -> float: return 0.0
    def advertised_leases(self, now: float) -> list[dict]: return []


class Carrier(Participant):
    """A participant that carries legs: every mesh node, and the coordinator
    for central legs."""

    def __init__(self, identity, wire, trust, **kw):
        super().__init__(identity, wire, trust, **kw)
        self._dispatch.update({"req": self._on_req, "resp": self._on_resp, "nack": self._on_nack})

    def _leg(self, src: str, decision: dict, leg_i, hop, *, toward_provider: bool) -> dict | None:
        """The leg a frame travels on, if the frame is where the signed decision
        says: at this node's place on the path, handed over by the node before it
        (toward the model node) or after it (back toward the decider)."""
        if not is_int(leg_i) or not is_int(hop) or not 0 <= leg_i < len(decision["legs"]):
            return None
        path = decision["legs"][leg_i]["path"]
        if not 0 <= hop < len(path) or path[hop] != self.did:
            return None
        if toward_provider:
            ok = hop >= 1 and src == path[hop - 1]
        else:
            ok = hop <= len(path) - 2 and src == path[hop + 1]
        return decision["legs"][leg_i] if ok else None

    def may_carry(self, decision: dict, now: float) -> str | None:
        """Why this node will not carry a leg of ``decision`` now, or None."""
        if self.ledger.excludes_node(decision["decider"]):
            return "the decider is latched"
        if self.ledger.lockdown and decision["mode"] != MODE_LOCKDOWN:
            return "lockdown: the decision layer is suspended"
        return None

    def _on_req(self, src: str, f: dict) -> None:
        now = float(self.clock())
        request, decision = f.get("request"), f.get("decision")
        if check_request(request, self.trust, now=now) or check_decision(decision, self.trust, request=request):
            return
        leg = self._leg(src, decision, f.get("leg"), f.get("hop"), toward_provider=True)
        if leg is None:
            return
        hop, path = f["hop"], leg["path"]
        if hop == len(path) - 1:
            self.serve(request, decision, f["leg"], now)
            return
        nxt = path[hop + 1]
        why = self.may_carry(decision, now)
        if why is None and self.ledger.excludes_node(nxt):
            why = "the next hop is latched"
        if why is None and not self.send(nxt, "req", request=request, decision=decision, leg=f["leg"], hop=hop + 1):
            why = "the next hop is unreachable"
        if why:
            self.nack(decision, f["leg"], hop, unreachable=nxt, reason=why)

    def nack(self, decision: dict, leg_i: int, hop: int, *, unreachable: str, reason: str) -> None:
        """Turn a leg back toward its decider."""
        if hop == 0:
            self.leg_failed(decision, leg_i, unreachable, reason)
            return
        prev = decision["legs"][leg_i]["path"][hop - 1]
        self.send(prev, "nack", decision=decision, leg=leg_i, hop=hop - 1, at=self.did,
                  unreachable=unreachable, reason=reason[:MAX_REASON])

    def _on_nack(self, src: str, f: dict) -> None:
        decision = f.get("decision")
        if check_decision(decision, self.trust):
            return
        leg = self._leg(src, decision, f.get("leg"), f.get("hop"), toward_provider=False)
        if leg is None:
            return
        unreachable = f["unreachable"] if is_did(f.get("unreachable")) else src
        at = f["at"] if is_did(f.get("at")) else src
        reason = f["reason"][:MAX_REASON] if isinstance(f.get("reason"), str) else ""
        if f["hop"] == 0:
            self.leg_failed(decision, f["leg"], unreachable, reason)
        else:
            self.send(leg["path"][f["hop"] - 1], "nack", decision=decision, leg=f["leg"], hop=f["hop"] - 1,
                      at=at, unreachable=unreachable, reason=reason)

    def _on_resp(self, src: str, f: dict) -> None:
        decision, response = f.get("decision"), f.get("response")
        if check_decision(decision, self.trust) or check_response(response, self.trust, decision=decision):
            return
        leg = self._leg(src, decision, f.get("leg"), f.get("hop"), toward_provider=False)
        if leg is None or leg["provider"] != response["provider"]:
            return
        if f["hop"] == 0:
            self.collect(decision, f["leg"], response)
        else:
            self.send(leg["path"][f["hop"] - 1], "resp", decision=decision, response=response, leg=f["leg"],
                      hop=f["hop"] - 1)

    # -- hooks for the roles built on top -------------------------------------------

    def serve(self, request: dict, decision: dict, leg_i: int, now: float) -> None: ...
    def leg_failed(self, decision: dict, leg_i: int, unreachable: str, reason: str) -> None: ...
    def collect(self, decision: dict, leg_i: int, response: dict) -> None: ...


__all__ = ["Config", "Participant", "Carrier", "FLOODED"]
