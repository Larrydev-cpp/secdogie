"""The signed wire objects: what verifies, what does not, and that no
signature does double duty across object types."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_identity import ALLOW_ANY, Allowlist, Identity  # noqa: E402
from secdogie_identity.signing import sign_payload  # noqa: E402
from secdogie_inference.protocol import (  # noqa: E402
    MODE_LOCKDOWN,
    MODE_NORMAL,
    VIA_CENTRAL,
    VIA_MESH,
    TrustRoots,
    check_advisory,
    check_decision,
    check_fault,
    check_heartbeat,
    check_request,
    check_response,
    decision_violations,
    digest,
    frame,
    make_advisory,
    make_decision,
    make_fault,
    make_heartbeat,
    make_request,
    make_response,
    parse_frame,
)
from secdogie_inference.router import Leg  # noqa: E402

NOW = 1_000_000.0


def clock():
    return NOW


@pytest.fixture
def cast():
    ids = {n: Identity.generate() for n in ("user", "s", "p", "q", "c", "x")}
    did = {n: i.did for n, i in ids.items()}
    trust = TrustRoots(mesh=Allowlist({did["s"], did["p"], did["q"], did["c"]}), users=Allowlist({did["user"]}),
                       deciders=Allowlist({did["s"]}), coordinators=Allowlist({did["c"]}),
                       brokers=Allowlist(), operators=Allowlist())
    return ids, did, trust


def test_trust_roots_refuse_none():
    with pytest.raises(ValueError, match="TrustRoots.users needs an allowlist"):
        TrustRoots(mesh=ALLOW_ANY, users=None, deciders=ALLOW_ANY, coordinators=ALLOW_ANY, brokers=ALLOW_ANY,
                   operators=ALLOW_ANY)


def test_request_verifies_and_rejects_tampering(cast):
    ids, did, trust = cast
    req = make_request(ids["user"], ["m/b", "m/a"], "hi", scopes=["observe.read"], quorum=1, clock=clock)
    assert req["models"] == ["m/a", "m/b"]
    assert check_request(req, trust, now=NOW) is None
    assert check_request({**req, "input": "bye"}, trust, now=NOW) == "invalid or missing signature"
    assert check_request(req, trust, now=NOW + 61) == "expired"
    assert check_request(req, trust, now=NOW - 500) == "issued in the future"
    stranger = make_request(ids["x"], ["m/a"], "hi", clock=clock)
    assert check_request(stranger, trust, now=NOW) == "signer not trusted"


@pytest.mark.parametrize("kw, reason", [
    ({"scopes": ["process.run_elevated"]}, "a scope is not grantable"),
    ({"quorum": 4}, "quorum must be 1..3"),
    ({"ttl": 1000}, "bad validity window"),
])
def test_request_shape_rules(cast, kw, reason):
    ids, _did, _trust = cast
    with pytest.raises(ValueError, match=reason):
        make_request(ids["user"], ["m/a"], "hi", clock=clock, **kw)


def test_pins_must_name_requested_models_and_cover_the_quorum(cast):
    ids, did, _trust = cast
    with pytest.raises(ValueError, match="a pin must be"):
        make_request(ids["user"], ["m/a"], "hi", pins=[(did["p"], "m/other")], clock=clock)
    with pytest.raises(ValueError, match="fewer pins than the quorum"):
        make_request(ids["user"], ["m/a"], "hi", quorum=2, pins=[(did["p"], "m/a")], clock=clock)
    with pytest.raises(ValueError, match="pinned twice"):
        make_request(ids["user"], ["m/a"], "hi", pins=[(did["p"], "m/a"), (did["p"], "m/a")], clock=clock)


def test_a_signature_never_crosses_object_types(cast):
    ids, did, trust = cast
    hb = make_heartbeat(ids["s"], roles=["serve"], seq=1, neighbors=[], clock=clock)
    assert check_heartbeat(hb, trust, now=NOW) is None
    relabelled = {**hb, "type": "secdogie/inference/request/v1"}
    assert check_request(relabelled, trust, now=NOW) is not None
    assert check_advisory(hb, trust, now=NOW) == "wrong or missing type"


def _decision(ids, did, req, legs, *, mode=MODE_NORMAL, signer="s"):
    return make_decision(ids[signer], req, legs, mode=mode, epoch=0, clock=clock)


def test_decision_shape(cast):
    ids, did, trust = cast
    req = make_request(ids["user"], ["m/a"], "hi", clock=clock)
    good = _decision(ids, did, req, [Leg(did["p"], "m/a", (did["s"], did["p"]))])
    assert check_decision(good, trust, request=req) is None
    other = make_request(ids["user"], ["m/a"], "other", clock=clock)
    assert check_decision(good, trust, request=other) == "decision is for a different request"
    bad = {
        "the decider is not the path's start": [Leg(did["p"], "m/a", (did["q"], did["p"]))],
        "a central leg through a non-coordinator": [Leg(did["p"], "m/a", (did["s"], did["q"], did["p"]), VIA_CENTRAL)],
        "a mesh leg through the coordinator": [Leg(did["p"], "m/a", (did["s"], did["c"], did["p"]), VIA_MESH)],
        "one provider on two legs": [Leg(did["p"], "m/a", (did["s"], did["p"])),
                                     Leg(did["p"], "m/a", (did["s"], did["q"], did["p"]))],
        "a loop": [Leg(did["p"], "m/a", (did["s"], did["q"], did["s"], did["p"]))],
    }
    for why, legs in bad.items():
        assert check_decision(_decision(ids, did, req, legs), trust, request=req) is not None, why
    central = _decision(ids, did, req, [Leg(did["p"], "m/a", (did["s"], did["c"], did["p"]), VIA_CENTRAL)])
    assert check_decision(central, trust, request=req) is None


def test_only_deciders_sign_ordinary_decisions_any_mesh_node_signs_lockdown_ones(cast):
    ids, did, trust = cast
    req = make_request(ids["user"], ["m/a"], "hi", quorum=2, pins=[(did["p"], "m/a"), (did["c"], "m/a")],
                       clock=clock)
    legs = [Leg(did["p"], "m/a", (did["q"], did["p"])), Leg(did["c"], "m/a", (did["q"], did["c"]))]
    assert check_decision(_decision(ids, did, req, legs, signer="q"), trust, request=req) == "signer not trusted"
    lockdown = _decision(ids, did, req, legs, signer="q", mode=MODE_LOCKDOWN)
    assert check_decision(lockdown, trust, request=req) is None
    assert decision_violations(lockdown, req) == []


def test_decision_violations_are_what_a_decider_does_against_the_request(cast):
    ids, did, _trust = cast
    req = make_request(ids["user"], ["m/a", "m/b"], "hi", quorum=2, clock=clock)
    one = _decision(ids, did, req, [Leg(did["p"], "m/a", (did["s"], did["p"]))])
    assert decision_violations(one, req) == ["quorum-downgrade"]
    swapped = _decision(ids, did, req, [Leg(did["p"], "m/z", (did["s"], did["p"])),
                                        Leg(did["q"], "m/a", (did["s"], did["q"]))])
    assert decision_violations(swapped, req) == ["model-substitution"]
    pinned = make_request(ids["user"], ["m/a"], "hi", pins=[(did["q"], "m/a")], clock=clock)
    ignored = _decision(ids, did, pinned, [Leg(did["p"], "m/a", (did["s"], did["p"]))])
    assert decision_violations(ignored, pinned) == ["pin-violation"]
    long_lockdown = _decision(ids, did, req, [Leg(did["p"], "m/a", (did["s"], did["q"], did["p"])),
                                              Leg(did["c"], "m/b", (did["s"], did["c"]))], mode=MODE_LOCKDOWN)
    assert "lockdown-shape" in decision_violations(long_lockdown, req)


def test_response_binds_request_decision_and_leg(cast):
    ids, did, trust = cast
    req = make_request(ids["user"], ["m/a"], "hi", clock=clock)
    dec = _decision(ids, did, req, [Leg(did["p"], "m/a", (did["s"], did["p"]))])
    resp = make_response(ids["p"], req, dec, "m/a", {"lease": "opaque"}, output="ok", clock=clock)
    assert check_response(resp, trust, request=req, decision=dec) is None
    other = _decision(ids, did, req, [Leg(did["q"], "m/a", (did["s"], did["q"]))])
    assert check_response(resp, trust, decision=other) == "response is for a different decision"
    stray = make_response(ids["q"], req, dec, "m/a", {}, output="ok", clock=clock)
    assert check_response(stray, trust, decision=dec) == "the decision gave this provider no leg for this model"
    refused = make_response(ids["p"], req, dec, "m/a", {}, output="secret", status="refused", reason="no", clock=clock)
    assert refused["output"] == ""


def test_heartbeat_rules(cast):
    ids, did, trust = cast
    assert check_heartbeat(make_heartbeat(ids["s"], roles=["serve"], seq=1, neighbors=[did["p"]], clock=clock),
                           trust, now=NOW) is None
    future = make_heartbeat(ids["s"], roles=[], seq=1, neighbors=[], clock=lambda: NOW + 500)
    assert check_heartbeat(future, trust, now=NOW) == "issued in the future"
    forged = sign_payload(ids["s"], {"type": "secdogie/inference/heartbeat/v1", "node": did["s"], "roles": ["god"],
                                     "seq": 1, "neighbors": [], "load": 0.0, "leases": [], "issued_at": NOW})
    assert check_heartbeat(forged, trust, now=NOW) == "bad roles"


def test_advisory_and_fault(cast):
    ids, did, trust = cast
    adv = make_advisory(ids["c"], epoch=3, failed=[did["q"]], bypass=[(did["s"], did["p"])], clock=clock)
    assert check_advisory(adv, trust, now=NOW) is None
    assert check_advisory(make_advisory(ids["s"], epoch=3, clock=clock), trust, now=NOW) == "signer not trusted"
    fault = make_fault(ids["s"], did["q"], "timeout", clock=clock)
    assert check_fault(fault, trust, now=NOW) is None
    assert check_fault(fault, trust, now=NOW + 1000) == "stale or future report"
    assert check_fault(make_fault(ids["s"], did["s"], "timeout", clock=clock), trust, now=NOW) == "bad accused"
    # an accusation against a DID outside the mesh is refused, so no reporter can grow the
    # coordinator's records without bound
    assert check_fault(make_fault(ids["s"], did["x"], "timeout", clock=clock), trust, now=NOW) == "bad accused"
    with pytest.raises(ValueError):
        make_fault(ids["s"], did["q"], "because", clock=clock)


def test_frames():
    raw = frame("nack", reason="x")
    assert parse_frame(raw) == {"k": "nack", "reason": "x"}
    assert parse_frame(b'{"k":"shell"}') is None
    assert parse_frame(b"\xff") is None
    with pytest.raises(ValueError):
        frame("shell")


def test_digest_is_order_independent():
    assert digest({"a": 1, "b": [1, 2]}) == digest({"b": [1, 2], "a": 1})
