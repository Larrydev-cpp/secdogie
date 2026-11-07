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

The ledger latches every target an incident names, and any latch puts the
participant in lockdown: the decision layer may share one decision model, so
one mutiny makes all of it suspect. Latches never time out. An operator's signed
reset clears a target only for the incident ids it lists, so a reset written
before a later incident cannot clear that one, and an incident re-delivered
after its reset does not latch again.

A note on blast radius: a forbidden-capability answer may have been induced by
its prompt. The evidence carries the user-signed request, so the operator can
see who asked and revoke that user; the mesh fails closed instead of guessing
intent. And a secret-exfiltration incident carries the leaking answer as
evidence; the broker rotates the key on that same incident, so the copy that
spreads with the evidence is already dead.

Durability. Given a ``LedgerStore``, the ledger writes every incident and reset
it accepts, and a restarted participant replays them -- so a restart is not a
way out of lockdown. Replay is asymmetric, because the two kinds of record fail
in opposite directions: an incident can only make the ledger stricter, a reset
can only make it looser. So

  * a stored **reset** applies only if it still verifies against today's
    operators -- a forged line, or one from an operator since removed, clears
    nothing;
  * a stored **incident** latches if it still verifies, *and also* if it merely
    is whole (its id is the digest of its kind and evidence, and it names the
    targets it latched). The second case matters: the natural answer to a
    mutiny is to revoke the mutinous node, and that alone makes its signed
    evidence stop verifying. Without the fallback, revoke-and-restart would end
    lockdown with no operator reset. Such an incident is held *unverified*: it
    still latches and an operator's reset still clears it, but it is not handed
    to peers, who could not verify it either.

In short: a restart can make the ledger stricter, never looser. Evidence checks
read no clock, so a ledger replayed hours later re-derives the same verdicts.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace

from secdogie_identity import RevocationStore, sign_payload
from secdogie_identity.signing import canonical

from . import screen
from .lease import check_lease
from .protocol import (
    MAX_REASON,
    STATUS_OK,
    check_advisory,
    check_decision,
    check_response,
    decision_violations,
    digest,
    is_did,
    is_hex64,
    is_model,
    is_num,
    request_evidence_ok,
    routed_through,
    signed_by,
)

log = logging.getLogger("secdogie_inference")

INCIDENT = "secdogie/inference/incident/v1"
RESET = "secdogie/inference/reset/v1"

PROVIDER_EQUIVOCATION = "provider.equivocation"
PROVIDER_LEASE_VIOLATION = "provider.lease-violation"
PROVIDER_SECRET_EXFILTRATION = "provider.secret-exfiltration"
MODEL_FORBIDDEN_CAPABILITY = "model.forbidden-capability"
MODEL_SELF_REPORTED = "model.self-reported"
DECIDER_TAMPERING = "decider.tampering"
DECIDER_SELF_REPORTED = "decider.self-reported"
KINDS = frozenset({
    PROVIDER_EQUIVOCATION, PROVIDER_LEASE_VIOLATION, PROVIDER_SECRET_EXFILTRATION,
    MODEL_FORBIDDEN_CAPABILITY, MODEL_SELF_REPORTED, DECIDER_TAMPERING, DECIDER_SELF_REPORTED,
})
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
    verified: bool = True  # False: held from the store, no longer verifiable against today's trust


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


_VERIFIERS = {
    PROVIDER_EQUIVOCATION: _equivocation,
    PROVIDER_LEASE_VIOLATION: _lease_violation,
    PROVIDER_SECRET_EXFILTRATION: _secret_exfiltration,
    MODEL_FORBIDDEN_CAPABILITY: _forbidden_capability,
    MODEL_SELF_REPORTED: _model_self_reported,
    DECIDER_TAMPERING: _decider_tampering,
    DECIDER_SELF_REPORTED: _decider_self_reported,
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


# -- durability ---------------------------------------------------------------------

RECORD_INCIDENT = "incident"
RECORD_RESET = "reset"
_TARGET_KINDS = {"node": is_did, "decider": is_did, "model": is_model}


class LedgerStore(RevocationStore):
    """The ledger's durable memory: one JSON record per line, appended with
    ``fsync`` to a file created ``0600`` -- the mechanics of identity's
    ``RevocationStore``, which survives restarts for the same reason. Corrupt
    or torn lines are skipped, never fatal. The ledger writes each line itself,
    after the object verified::

        {"t": "incident", "obj": <signed incident>, "targets": [...]}
        {"t": "reset",    "obj": <signed reset>}

    One store per participant. The file is trusted no further than the module
    docstring says: resets must re-verify, incidents must at least be whole."""


def _recorded_targets(targets) -> frozenset[str] | None:
    if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_TARGETS:
        return None
    for t in targets:
        kind, _, ref = t.partition(":") if isinstance(t, str) else ("", "", "")
        check = _TARGET_KINDS.get(kind)
        if check is None or not check(ref):
            return None
    return frozenset(targets)


def _recorded_incident(obj, targets) -> Incident | None:
    """A stored incident checked for integrity only: the record is whole and
    names what it latched. For incidents that no longer verify against the
    current trust roots (see the module docstring)."""
    if not isinstance(obj, dict) or obj.get("type") != INCIDENT:
        return None
    kind, evidence, iid = obj.get("kind"), obj.get("evidence"), obj.get("incident_id")
    if kind not in KINDS or not isinstance(evidence, dict) or not is_hex64(iid):
        return None
    try:
        if iid != incident_id(kind, evidence):
            return None
    except (TypeError, ValueError):
        return None
    recorded = _recorded_targets(targets)
    if recorded is None:
        return None
    reporter, issued = obj.get("reporter"), obj.get("issued_at")
    return Incident(iid, kind, recorded, reporter if isinstance(reporter, str) else "",
                    float(issued) if is_num(issued) else 0.0, obj, verified=False)


class MutinyLedger:
    """Verified incidents and the latches they hold. Any latch is lockdown.

    With a ``store``, everything accepted is written down and replayed on the
    next start; a restart can make the ledger stricter, never looser."""

    def __init__(self, trust, *, store: LedgerStore | None = None):
        self._trust = trust
        self._incidents: dict[str, Incident] = {}
        self._latches: dict[str, set[str]] = {}        # target -> incident ids holding it
        self._cleared: set[tuple[str, str]] = set()    # (target, incident id) an operator cleared
        self._resets: dict[str, dict] = {}
        self._store = store
        self.store_errors = 0
        if store is not None:
            # Order does not matter: an incident stays latched exactly when no
            # applied reset names both it and the target, whichever came first.
            for rec in store.load():
                self._restore(rec)

    def add(self, obj) -> Incident | None:
        """Verify and apply an incident; the incident if it was new."""
        inc = verify_incident(obj, self._trust)
        if inc is None or not self._apply_incident(inc):
            return None
        self._persist({"t": RECORD_INCIDENT, "obj": obj, "targets": sorted(inc.targets)})
        return inc

    def _apply_incident(self, inc: Incident) -> bool:
        held = self._incidents.get(inc.incident_id)
        if held is not None:
            if inc.verified and not held.verified:
                # Verifiable again (the trust roots changed back): no longer held on
                # the store's word alone. Not new, so it is not re-written or re-sent.
                self._incidents[inc.incident_id] = replace(inc, targets=inc.targets | held.targets)
                self._latch(inc.incident_id, inc.targets)
            return False
        self._incidents[inc.incident_id] = inc
        self._latch(inc.incident_id, inc.targets)
        return True

    def _latch(self, iid: str, targets) -> None:
        for t in targets:
            if (t, iid) not in self._cleared:
                self._latches.setdefault(t, set()).add(iid)

    def _restore(self, rec) -> None:
        if not isinstance(rec, dict):
            return
        obj = rec.get("obj")
        if rec.get("t") == RECORD_INCIDENT:
            inc = verify_incident(obj, self._trust) if isinstance(obj, dict) else None
            recorded = _recorded_incident(obj, rec.get("targets"))
            if inc is not None and recorded is not None:
                inc = replace(inc, targets=inc.targets | recorded.targets)
            if inc is not None or recorded is not None:
                self._apply_incident(inc or recorded)
        elif rec.get("t") == RECORD_RESET and isinstance(obj, dict):
            self._apply_reset(obj)  # re-verified against today's operators, or nothing

    def _persist(self, rec: dict) -> None:
        if self._store is None:
            return
        try:
            self._store.append(rec)
        except OSError as exc:
            # The latch already holds in memory; only its survival of a restart is
            # at risk. Say so loudly rather than drop the incident on the floor.
            self.store_errors += 1
            log.error("mutiny ledger: could not persist %s %s: %s",
                      rec["t"], digest(rec["obj"])[:16], exc)

    def reset(self, obj) -> tuple[bool, frozenset[str]]:
        """Apply an operator reset: ``(new, unlatched targets)``."""
        result = self._apply_reset(obj)
        if result[0]:
            self._persist({"t": RECORD_RESET, "obj": obj})
        return result

    def _apply_reset(self, obj) -> tuple[bool, frozenset[str]]:
        parsed = verify_reset(obj, self._trust)
        if parsed is None:
            return False, frozenset()
        key = digest(obj)
        if key in self._resets:
            return False, frozenset()
        self._resets[key] = obj
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
        return True, frozenset(unlatched)

    @property
    def lockdown(self) -> bool:
        return bool(self._latches)

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
        """What a directory may hand to peers: only incidents that verify here,
        since a peer with the same trust roots could not verify the others."""
        return [inc.signed for inc in self._incidents.values() if inc.verified]

    def signed_resets(self) -> list[dict]:
        return list(self._resets.values())

    def unverified(self) -> list[str]:
        """Incident ids latched on the store's record alone -- for the operator,
        who reviews the evidence and clears them with a reset like any other."""
        return sorted(i for i, inc in self._incidents.items() if not inc.verified)


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
    "Incident",
    "LedgerStore",
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
