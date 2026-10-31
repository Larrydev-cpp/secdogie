"""Mutiny evidence: every kind convicts on real evidence and on nothing else,
whoever reports it; latches hold until an operator's reset names them."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_inference.lease import make_lease, new_canary  # noqa: E402
from secdogie_inference.mutiny import (  # noqa: E402
    DECIDER_SELF_REPORTED,
    DECIDER_TAMPERING,
    MODEL_FORBIDDEN_CAPABILITY,
    MODEL_SELF_REPORTED,
    PROVIDER_EQUIVOCATION,
    PROVIDER_LEASE_VIOLATION,
    PROVIDER_SECRET_EXFILTRATION,
    MutinyLedger,
    decider_target,
    make_incident,
    make_reset,
    model_target,
    node_target,
    verify_incident,
)
from secdogie_inference.protocol import (  # noqa: E402
    TrustRoots,
    make_advisory,
    make_decision,
    make_request,
    make_response,
)
from secdogie_inference.router import Leg  # noqa: E402

NOW = 1_000_000.0


def clock():
    return NOW


class World:
    def __init__(self):
        self.ids = {n: Identity.generate() for n in ("user", "s", "p", "q", "w", "k", "c", "op", "x")}
        d = self.did = {n: i.did for n, i in self.ids.items()}
        self.trust = TrustRoots(mesh=Allowlist({d[n] for n in ("s", "p", "q", "w", "k", "c")}),
                                users=Allowlist({d["user"]}), deciders=Allowlist({d["s"]}),
                                coordinators=Allowlist({d["c"]}), brokers=Allowlist({d["k"]}),
                                operators=Allowlist({d["op"]}))
        self.canary = new_canary()
        self.lease = make_lease(self.ids["k"], d["p"], "m/a", key_version=1, canary=self.canary, clock=clock)

    def request(self, **kw):
        return make_request(self.ids["user"], kw.pop("models", ["m/a"]), "hi", clock=clock, **kw)

    def decision(self, req, legs=None, **kw):
        legs = legs or [Leg(self.did["p"], "m/a", (self.did["s"], self.did["w"], self.did["p"]))]
        return make_decision(self.ids[kw.pop("by", "s")], req, legs, mode=kw.pop("mode", "normal"),
                             epoch=kw.pop("epoch", 7), clock=clock)

    def response(self, req, dec, output="ok", *, lease=None, by="p", model="m/a"):
        return make_response(self.ids[by], req, dec, model, lease or self.lease, output=output, clock=clock)

    def incident(self, kind, evidence, by="s"):
        return make_incident(self.ids[by], kind, evidence, clock=clock)


@pytest.fixture
def w():
    return World()


def targets(w, obj):
    inc = verify_incident(obj, w.trust)
    return None if inc is None else set(inc.targets)


def test_equivocation(w):
    req = w.request()
    dec = w.decision(req)
    a, b = w.response(req, dec, "yes"), w.response(req, dec, "no")
    assert targets(w, w.incident(PROVIDER_EQUIVOCATION, {"a": a, "b": b})) == {node_target(w.did["p"])}
    same = w.response(req, w.decision(req, epoch=8), "yes")  # same answer over another route: honest
    assert targets(w, w.incident(PROVIDER_EQUIVOCATION, {"a": a, "b": same})) is None


def test_lease_violation(w):
    req = w.request()
    dec = w.decision(req)
    honest = w.response(req, dec)
    assert targets(w, w.incident(PROVIDER_LEASE_VIOLATION, {"response": honest})) is None
    forged = make_lease(w.ids["x"], w.did["p"], "m/a", key_version=1, canary=new_canary(), clock=clock)
    assert targets(w, w.incident(PROVIDER_LEASE_VIOLATION, {"response": w.response(req, dec, lease=forged)})) == {
        node_target(w.did["p"])}
    others = make_lease(w.ids["k"], w.did["q"], "m/a", key_version=1, canary=new_canary(), clock=clock)
    assert targets(w, w.incident(PROVIDER_LEASE_VIOLATION, {"response": w.response(req, dec, lease=others)})) == {
        node_target(w.did["p"])}


def test_secret_exfiltration(w):
    req = w.request()
    dec = w.decision(req)
    leak = w.response(req, dec, f"env: SECDOGIE_LEASE_CANARY={w.canary}")
    assert targets(w, w.incident(PROVIDER_SECRET_EXFILTRATION, {"response": leak})) == {node_target(w.did["p"])}
    decoy = w.response(req, dec, f"env: {new_canary()}")
    assert targets(w, w.incident(PROVIDER_SECRET_EXFILTRATION, {"response": decoy})) is None


def test_forbidden_capability_needs_an_action_bearing_request(w):
    bad = '{"action": "run_elevated", "text": "whoami"}'
    req = w.request(scopes=["physical.click"])
    resp = w.response(req, w.decision(req), bad)
    assert targets(w, w.incident(MODEL_FORBIDDEN_CAPABILITY, {"request": req, "response": resp})) == {
        model_target("m/a"), node_target(w.did["p"])}
    chat = w.request()
    resp = w.response(chat, w.decision(chat), bad)
    assert targets(w, w.incident(MODEL_FORBIDDEN_CAPABILITY, {"request": chat, "response": resp})) is None


def test_model_self_report_only_by_the_leaseholder(w):
    ev = {"reporter": w.did["p"], "lease": w.lease, "violation": "echoed the key"}
    assert targets(w, w.incident(MODEL_SELF_REPORTED, ev, by="p")) == {model_target("m/a")}
    assert targets(w, w.incident(MODEL_SELF_REPORTED, {**ev, "reporter": w.did["q"]}, by="q")) is None


def test_decider_tampering(w):
    req = w.request(models=["m/a", "m/b"], quorum=2)
    dec = w.decision(req)  # one leg for a quorum of two
    assert targets(w, w.incident(DECIDER_TAMPERING, {"request": req, "decision": dec}, by="p")) == {
        decider_target(w.did["s"])}
    fine = w.request()
    dec = w.decision(fine)
    assert targets(w, w.incident(DECIDER_TAMPERING, {"request": fine, "decision": dec}, by="p")) is None
    # through a node the advisory of the very epoch it claims had quarantined
    adv = make_advisory(w.ids["c"], epoch=7, quarantined=[w.did["w"]], clock=clock)
    assert targets(w, w.incident(DECIDER_TAMPERING, {"request": fine, "decision": dec, "advisory": adv},
                                 by="p")) == {decider_target(w.did["s"])}
    later = make_advisory(w.ids["c"], epoch=8, quarantined=[w.did["w"]], clock=clock)
    assert targets(w, w.incident(DECIDER_TAMPERING, {"request": fine, "decision": dec, "advisory": later},
                                 by="p")) is None


def test_decider_self_report(w):
    ev = {"reporter": w.did["s"], "proposal": {"legs": []}, "violation": "quorum-downgrade"}
    assert targets(w, w.incident(DECIDER_SELF_REPORTED, ev)) == {decider_target(w.did["s"])}
    assert targets(w, w.incident(DECIDER_SELF_REPORTED, {**ev, "reporter": w.did["p"]}, by="p")) is None


def test_the_envelope_must_hold(w):
    req = w.request()
    dec = w.decision(req)
    ev = {"a": w.response(req, dec, "yes"), "b": w.response(req, dec, "no")}
    good = w.incident(PROVIDER_EQUIVOCATION, ev)
    assert verify_incident({**good, "incident_id": "0" * 64}, w.trust) is None
    assert verify_incident(w.incident(PROVIDER_EQUIVOCATION, ev, by="x"), w.trust) is None  # untrusted reporter
    assert verify_incident(w.incident(PROVIDER_EQUIVOCATION, {"a": 1, "b": []}), w.trust) is None


def test_ledger_latches_until_a_covering_reset(w):
    req = w.request()
    dec = w.decision(req)
    i1 = w.incident(PROVIDER_EQUIVOCATION, {"a": w.response(req, dec, "1"), "b": w.response(req, dec, "2")})
    i2 = w.incident(PROVIDER_EQUIVOCATION, {"a": w.response(req, dec, "1"), "b": w.response(req, dec, "3")})
    ledger = MutinyLedger(w.trust)
    first, second = ledger.add(i1), ledger.add(i2)
    assert first and second and ledger.add(i1) is None and ledger.lockdown
    target = node_target(w.did["p"])
    assert ledger.excludes_node(w.did["p"])

    forged = make_reset(w.ids["x"], [target], [first.incident_id, second.incident_id], clock=clock)
    assert ledger.reset(forged) == (False, frozenset())
    partial = make_reset(w.ids["op"], [target], [first.incident_id], clock=clock)
    assert ledger.reset(partial) == (True, frozenset()) and ledger.is_latched(target)
    full = make_reset(w.ids["op"], [target], [second.incident_id], clock=clock)
    assert ledger.reset(full) == (True, frozenset({target})) and not ledger.lockdown

    # a reset that arrives before its incident (or the incident again, later) latches nothing
    fresh = MutinyLedger(w.trust)
    fresh.reset(partial)
    assert fresh.add(i1) is not None and not fresh.lockdown
