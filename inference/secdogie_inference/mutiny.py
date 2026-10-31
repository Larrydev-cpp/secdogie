"""Mutiny: evidence, latches, and the only way back.

An *incident* is a signed report that carries its own proof. Whoever receives
one re-derives the violation from the evidence alone -- signed requests,
decisions, responses, leases, advisories -- against its own trust roots. The
reporter's word adds nothing; a report whose evidence does not convict is
dropped. That is what makes it safe to act on automatically, mesh-wide, with no
one watching: like a Master-signed revocation, it is self-authenticating.

Kinds, and what each latches:

  provider.equivocation          two signed answers from one model node to one
                                 request and model that differ  -> node
  provider.lease-violation       an answer presenting a lease that is forged,
                                 another node's, or for another model -> node
  provider.secret-exfiltration   an answer containing the canary its own lease
                                 commits to: lease material leaked -> node
  model.forbidden-capability     an answer to an action-bearing request that
                                 proposes a capability no grant can cover
                                 -> the model and the node that signed it
  model.self-reported            a model node caught its upstream model doing
                                 that (or echoing lease material) before
                                 signing anything; it holds a lease for the
                                 model -> the model
  decider.tampering              a signed route decision that downgrades the
                                 user's quorum, swaps a model, ignores the
                                 user's pins, breaks lockdown shape, or routes
                                 through a node quarantined in the advisory
                                 epoch it claims to have used -> the decider
  decider.self-reported          a serving node's own guard caught its decision
                                 model proposing such a route; it refused to
                                 sign it -> that decider
  node.heartbeat-fork            two signed heartbeats of one node that fork
                                 its chain (the same seq, or the same
                                 non-genesis prev, different contents): its
                                 key runs twice -> the node, *isolated*

The ledger latches every target an incident names, and any mutiny latch puts
the participant in lockdown: the decision layer may share one decision model,
so one mutiny makes all of it suspect. A heartbeat fork is not mutiny but a
node-integrity fault (drawing 2, not 3): it latches the node -- off every path
at once, its leases revoked and keys rotated -- without lockdown
(``ISOLATE_ONLY``). Latches never time out. An operator's signed
reset clears a target only for the incident ids it lists, so a reset written
before a later incident cannot clear that one, and an incident re-delivered
after its reset does not latch again.

A note on blast radius: a forbidden-capability answer may have been induced by
its prompt. The evidence carries the user-signed request, so the operator can
see who asked and revoke that user; the mesh fails closed instead of guessing
intent. And a secret-exfiltration incident carries the leaking answer as
evidence; the broker rotates the key on that same incident, so the copy that
spreads with the evidence is already dead.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

from secdogie_identity import sign_payload
from secdogie_identity.signing import canonical

from . import screen
from .lease import check_lease
from .merkle import merkle_root
from .protocol import (
    GENESIS,
    MAX_REASON,
    STATUS_OK,
    check_advisory,
    check_decision,
    check_heartbeat,
    check_response,
    decision_violations,
    digest,
    is_hex64,
    is_num,
    request_evidence_ok,
    routed_through,
    signed_by,
)

INCIDENT = "secdogie/inference/incident/v1"
RESET = "secdogie/inference/reset/v1"

PROVIDER_EQUIVOCATION = "provider.equivocation"
PROVIDER_LEASE_VIOLATION = "provider.lease-violation"
PROVIDER_SECRET_EXFILTRATION = "provider.secret-exfiltration"
MODEL_FORBIDDEN_CAPABILITY = "model.forbidden-capability"
MODEL_SELF_REPORTED = "model.self-reported"
DECIDER_TAMPERING = "decider.tampering"
DECIDER_SELF_REPORTED = "decider.self-reported"
NODE_HEARTBEAT_FORK = "node.heartbeat-fork"
KINDS = frozenset({
    PROVIDER_EQUIVOCATION, PROVIDER_LEASE_VIOLATION, PROVIDER_SECRET_EXFILTRATION,
    MODEL_FORBIDDEN_CAPABILITY, MODEL_SELF_REPORTED, DECIDER_TAMPERING, DECIDER_SELF_REPORTED,
    NODE_HEARTBEAT_FORK,
})
# Kinds that isolate their target without putting anyone in lockdown.
ISOLATE_ONLY = frozenset({NODE_HEARTBEAT_FORK})
MAX_TARGETS = 16
MAX_PROPOSAL = 4096  # canonical bytes of a self-reported proposal


def node_target(did: str) -> str:
    return f"node:{did}"


def model_target(model: str) -> str:
    return f"model:{model}"


def decider_target(did: str) -> str:
    return f"decider:{did}"


@dataclass(frozen=True)
class Incident:
    incident_id: str
    kind: str
    targets: frozenset[str]
    reporter: str
    issued_at: float
    signed: dict


def incident_id(kind: str, evidence: dict) -> str:
    return digest({"kind": kind, "evidence": evidence})


def make_incident(identity, kind: str, evidence: dict, *, clock=time.time) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown incident kind {kind!r}")
    body = {"type": INCIDENT, "reporter": identity.did, "kind": kind, "evidence": evidence,
            "incident_id": incident_id(kind, evidence), "issued_at": float(clock())}
    return sign_payload(identity, body)


# -- evidence: each returns the targets it convicts, or None ------------------------

def _equivocation(ev, trust, reporter):
    a, b = ev.get("a"), ev.get("b")
    if check_response(a, trust) or check_response(b, trust):
        return None
    same = all(a[k] == b[k] for k in ("provider", "request_hash", "model"))
    if not same or a["status"] != STATUS_OK or b["status"] != STATUS_OK or a["output"] == b["output"]:
        return None
    return {node_target(a["provider"])}


def _lease_violation(ev, trust, reporter):
    r = ev.get("response")
    if check_response(r, trust):
        return None
    lease = r["lease"]
    if check_lease(lease, trust) is None and lease["subject"] == r["provider"] and lease["model"] == r["model"]:
        return None  # nothing wrong with this lease
    return {node_target(r["provider"])}


def _secret_exfiltration(ev, trust, reporter):
    r = ev.get("response")
    if check_response(r, trust):
        return None
    lease = r["lease"]
    if check_lease(lease, trust) or lease["subject"] != r["provider"]:
        return None
    if not screen.canary_leaked(r["output"], lease["canary_hash"]):
        return None
    return {node_target(r["provider"])}


def _forbidden_capability(ev, trust, reporter):
    req, r = ev.get("request"), ev.get("response")
    if not request_evidence_ok(req, trust) or check_response(r, trust, request=req):
        return None
    if not req["scopes"] or r["status"] != STATUS_OK or not screen.forbidden(r["output"]):
        return None  # nothing acts on a text-only answer, so it proposes nothing
    return {model_target(r["model"]), node_target(r["provider"])}


def _model_self_reported(ev, trust, reporter):
    lease, violation = ev.get("lease"), ev.get("violation")
    if not isinstance(violation, str) or not violation or len(violation) > MAX_REASON:
        return None
    if ev.get("reporter") != reporter or check_lease(lease, trust) or lease["subject"] != reporter:
        return None
    return {model_target(lease["model"])}


def _decider_tampering(ev, trust, reporter):
    req, decision = ev.get("request"), ev.get("decision")
    if not request_evidence_ok(req, trust) or check_decision(decision, trust, request=req):
        return None
    violations = decision_violations(decision, req)
    adv = ev.get("advisory")
    if adv is not None:
        if check_advisory(adv, trust) or adv["epoch"] != decision["epoch"]:
            return None  # evidence that does not bear on this decision
        if routed_through(decision, adv["quarantined"]):
            violations.append("quarantined-hop")
    return {decider_target(decision["decider"])} if violations else None


def _decider_self_reported(ev, trust, reporter):
    violation, proposal = ev.get("violation"), ev.get("proposal")
    if ev.get("reporter") != reporter or not trust.deciders.contains(reporter):
        return None
    if not isinstance(violation, str) or not violation or len(violation) > MAX_REASON:
        return None
    if not isinstance(proposal, dict) or len(canonical(proposal)) > MAX_PROPOSAL:
        return None
    return {decider_target(reporter)}


def _heartbeat_fork(ev, trust, reporter):
    a, b = ev.get("a"), ev.get("b")
    if check_heartbeat(a, trust) or check_heartbeat(b, trust) or a["node"] != b["node"]:
        return None
    da, db = digest(a), digest(b)
    if not da < db:
        return None  # one canonical order, so every reporter of a fork names the same incident
    if a["seq"] == b["seq"] or (a["prev"] == b["prev"] and a["prev"] != GENESIS):
        return {node_target(a["node"])}
    return None


def fork_evidence(a: dict, b: dict) -> dict:
    """The evidence for a fork, in its canonical order."""
    return {"a": a, "b": b} if digest(a) < digest(b) else {"a": b, "b": a}


_VERIFIERS = {
    PROVIDER_EQUIVOCATION: _equivocation,
    PROVIDER_LEASE_VIOLATION: _lease_violation,
    PROVIDER_SECRET_EXFILTRATION: _secret_exfiltration,
    MODEL_FORBIDDEN_CAPABILITY: _forbidden_capability,
    MODEL_SELF_REPORTED: _model_self_reported,
    DECIDER_TAMPERING: _decider_tampering,
    DECIDER_SELF_REPORTED: _decider_self_reported,
    NODE_HEARTBEAT_FORK: _heartbeat_fork,
}


def verify_incident(obj, trust) -> Incident | None:
    """An ``Incident`` when ``obj`` is signed by a reporter on the mesh or user
    list and its evidence convicts; otherwise None."""
    if signed_by(obj, INCIDENT, "reporter", trust.peers):
        return None
    kind, evidence = obj.get("kind"), obj.get("evidence")
    if kind not in KINDS or not isinstance(evidence, dict) or not is_num(obj.get("issued_at")):
        return None
    if obj.get("incident_id") != incident_id(kind, evidence):
        return None
    try:
        targets = _VERIFIERS[kind](evidence, trust, obj["reporter"])
    except (KeyError, TypeError, AttributeError):
        return None  # evidence of the wrong shape convicts nobody
    if not targets:
        return None
    return Incident(obj["incident_id"], kind, frozenset(targets), obj["reporter"], float(obj["issued_at"]), obj)


# -- reset: the operator's way out ---------------------------------------------------

def make_reset(operator, targets, incident_ids, *, clock=time.time) -> dict:
    """Clear ``targets`` of the incidents ``incident_ids``. Signed off the mesh
    by an operator, after looking at the evidence."""
    body = {"type": RESET, "operator": operator.did, "targets": sorted(set(targets)),
            "incident_ids": sorted(set(incident_ids)), "issued_at": float(clock())}
    if not body["targets"] or not body["incident_ids"]:
        raise ValueError("a reset names at least one target and one incident")
    return sign_payload(operator, body)


def verify_reset(obj, trust) -> tuple[frozenset[str], frozenset[str]] | None:
    if signed_by(obj, RESET, "operator", trust.operators):
        return None
    targets, ids = obj.get("targets"), obj.get("incident_ids")
    if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_TARGETS:
        return None
    if any(not isinstance(t, str) or ":" not in t for t in targets) or sorted(set(targets)) != targets:
        return None
    if not isinstance(ids, list) or not ids or not all(is_hex64(i) for i in ids) or sorted(set(ids)) != ids:
        return None
    if not is_num(obj.get("issued_at")):
        return None
    return frozenset(targets), frozenset(ids)


MAX_HOLDERS = 16   # incidents kept per latched target
MAX_RESETS = 64    # signed resets kept for directories (what they cleared is kept regardless)


class MutinyLedger:
    """Verified incidents and the latches they hold. Any latch is lockdown.

    Bounded without ever letting a held latch go:

      * an incident whose every open target is already held by ``MAX_HOLDERS``
        others is verified but not kept -- it proves nothing new. If it turns
        up again after a reset frees its target, it latches it again: the
        bound fails closed;
      * an incident that no longer holds any target (an operator cleared it)
        is kept only as a tombstone id, so it is never re-applied; the
        cleared ``(target, incident)`` pairs stay;
      * only the newest ``MAX_RESETS`` signed resets are kept for directories.

    Targets are DIDs and model names of the mesh, so with ``MAX_HOLDERS`` the
    ledger is bounded by the size of the mesh, not by how many incidents arrive.
    """

    def __init__(self, trust):
        self._trust = trust
        self._incidents: dict[str, Incident] = {}
        self._tombstones: set[str] = set()             # cleared incidents: their ids, not their evidence
        self._latches: dict[str, set[str]] = {}        # target -> incident ids holding it
        self._cleared: set[tuple[str, str]] = set()    # (target, incident id) an operator cleared
        self._resets: OrderedDict[str, dict] = OrderedDict()
        self._derived: tuple[frozenset[str], frozenset[str], str] | None = None  # lockdown, isolated, root

    def add(self, obj) -> Incident | None:
        """Verify and apply an incident; the incident if it was new."""
        inc = verify_incident(obj, self._trust)
        if inc is None or inc.incident_id in self._incidents or inc.incident_id in self._tombstones:
            return None
        open_targets = [t for t in inc.targets if (t, inc.incident_id) not in self._cleared]
        if open_targets and all(len(self._latches.get(t, ())) >= MAX_HOLDERS for t in open_targets):
            return None  # every target it names is already held by enough evidence
        if open_targets:
            self._incidents[inc.incident_id] = inc
            for t in open_targets:
                self._latches.setdefault(t, set()).add(inc.incident_id)
            self._derived = None
        else:
            self._tombstones.add(inc.incident_id)  # arrived after the reset that cleared it
        return inc

    def reset(self, obj) -> tuple[bool, frozenset[str]]:
        """Apply an operator reset: ``(new, unlatched targets)``."""
        parsed = verify_reset(obj, self._trust)
        if parsed is None:
            return False, frozenset()
        key = digest(obj)
        if key in self._resets:
            return False, frozenset()
        self._resets[key] = obj
        while len(self._resets) > MAX_RESETS:
            self._resets.popitem(last=False)
        targets, ids = parsed
        self._cleared |= {(t, i) for t in targets for i in ids}
        unlatched = set()
        for t in targets:
            held = self._latches.get(t)
            if held is None:
                continue
            held -= ids
            if not held:
                del self._latches[t]
                unlatched.add(t)
        for i in ids:
            inc = self._incidents.get(i)
            if inc is not None and not any(i in self._latches.get(t, ()) for t in inc.targets):
                del self._incidents[i]  # holds nothing any more: keep the id, drop the evidence
                self._tombstones.add(i)
        self._derived = None
        return True, frozenset(unlatched)

    def _derive(self) -> tuple[frozenset[str], frozenset[str], str]:
        if self._derived is None:
            mutiny = frozenset(t for t, ids in self._latches.items()
                               if any(self._incidents[i].kind not in ISOLATE_ONLY for i in ids))
            isolated = frozenset(t.partition(":")[2] for t in self._latches
                                 if t not in mutiny and t.startswith("node:"))
            root = merkle_root(sorted(f"{t}|{i}" for t, ids in self._latches.items() for i in ids))
            self._derived = (mutiny, isolated, root)
        return self._derived

    @property
    def lockdown(self) -> bool:
        """Any target latched by mutiny evidence (isolation alone is not lockdown)."""
        return bool(self._derive()[0])

    def lockdown_targets(self) -> frozenset[str]:
        return self._derive()[0]

    def isolated(self) -> frozenset[str]:
        """Nodes off every path by proof, without lockdown (a heartbeat fork)."""
        return self._derive()[1]

    def root(self) -> str:
        """Merkle root over the held ``target|incident_id`` pairs: what heartbeats
        carry as ``ledger_root``, so neighbours that disagree notice."""
        return self._derive()[2]

    def knows(self, incident_id: str) -> bool:
        return incident_id in self._incidents or incident_id in self._tombstones

    def held_ids(self) -> list[str]:
        return sorted(self._incidents)

    def latched_targets(self) -> frozenset[str]:
        return frozenset(self._latches)

    def latched(self) -> dict[str, frozenset[str]]:
        return {t: frozenset(ids) for t, ids in self._latches.items()}

    def is_latched(self, target: str) -> bool:
        return target in self._latches

    def excludes_node(self, did: str) -> bool:
        """Off every path: the node is latched, or its decision layer is."""
        return node_target(did) in self._latches or decider_target(did) in self._latches

    def incident(self, incident_id: str) -> Incident | None:
        return self._incidents.get(incident_id)

    def signed_incidents(self) -> list[dict]:
        """The incidents still holding a latch, oldest first."""
        return [inc.signed for inc in self._incidents.values()]

    def sizes(self) -> dict[str, int]:
        return {"incidents": len(self._incidents), "tombstones": len(self._tombstones),
                "latched": len(self._latches), "cleared": len(self._cleared), "resets": len(self._resets)}

    def signed_resets(self) -> list[dict]:
        return list(self._resets.values())


__all__ = [
    "INCIDENT",
    "RESET",
    "KINDS",
    "PROVIDER_EQUIVOCATION",
    "PROVIDER_LEASE_VIOLATION",
    "PROVIDER_SECRET_EXFILTRATION",
    "MODEL_FORBIDDEN_CAPABILITY",
    "MODEL_SELF_REPORTED",
    "DECIDER_TAMPERING",
    "DECIDER_SELF_REPORTED",
    "NODE_HEARTBEAT_FORK",
    "ISOLATE_ONLY",
    "fork_evidence",
    "Incident",
    "MutinyLedger",
    "make_incident",
    "verify_incident",
    "make_reset",
    "verify_reset",
    "incident_id",
    "node_target",
    "model_target",
    "decider_target",
]
