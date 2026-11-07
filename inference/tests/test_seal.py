"""Sealed input: the prompt is readable by the model nodes it names and by no
one else on the way -- not the decider, the forwarders, the coordinator, nor
anyone an incident's evidence floods to."""
from __future__ import annotations

import base64
import json

import pytest

pytest.importorskip("nacl")

import sim  # noqa: E402
from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity, sign_payload  # noqa: E402
from secdogie_inference import (  # noqa: E402
    MODE_LOCKDOWN,
    OUTCOME_ACCEPTED,
    OUTCOME_REFUSED,
    Router,
    make_incident,
    make_request,
    open_input,
)
from secdogie_inference.lease import LeaseBook, lease_shape, make_lease, new_canary  # noqa: E402
from secdogie_inference.mutiny import DECIDER_SELF_REPORTED, decider_target  # noqa: E402
from secdogie_inference.protocol import (  # noqa: E402
    MAX_INPUT,
    MAX_RECIPIENTS,
    TrustRoots,
    frame,
    request_shape,
    sealed_to,
)
from secdogie_inference.router import Leg  # noqa: E402

ALPHA, BETA = sim.ALPHA, sim.BETA
SECRET = "my bank PIN is 4921 -- summarise my statement"
NOW = 1_000_000.0


def clock():
    return NOW


def b64(key: PrivateKey) -> str:
    return base64.b64encode(bytes(key.public_key)).decode()


class World:
    def __init__(self):
        self.ids = {n: Identity.generate() for n in ("alice", "bob", "p", "q", "x")}
        self.did = {n: i.did for n, i in self.ids.items()}
        self.keys = {n: PrivateKey.generate() for n in ("p", "q", "x")}

    def to(self, *names):
        return [(self.did[n], b64(self.keys[n])) for n in names]

    def sealed(self, who="alice", to=("p", "q"), **kw):
        return make_request(self.ids[who], ["m/a"], SECRET, seal_to=self.to(*to), clock=clock, **kw)


@pytest.fixture
def w():
    return World()


# -- the envelope ----------------------------------------------------------------------

def test_only_the_named_model_nodes_can_read_it(w):
    req = w.sealed()
    assert "input" not in req and SECRET not in json.dumps(req)
    assert request_shape(req) is None and sealed_to(req) == {w.did["p"], w.did["q"]}
    assert open_input(req, w.did["p"], w.keys["p"]) == (SECRET, None)
    assert open_input(req, w.did["q"], w.keys["q"]) == (SECRET, None)
    assert open_input(req, w.did["x"], w.keys["x"]) == (None, "the input is not sealed to this node")
    assert open_input(req, w.did["p"], w.keys["x"]) == (None, "the sealed input does not open")
    assert open_input(req, w.did["p"], None)[0] is None


def test_an_envelope_pasted_into_another_users_request_does_not_open(w):
    """Bob saw Alice's sealed request (incident evidence floods to users) and
    asks p the same thing under his own name, hoping to read the answer."""
    alices = w.sealed("alice")
    body = {k: v for k, v in alices.items() if k not in ("signer", "sig")}
    body.update(user=w.did["bob"], req_id="ab" * 16)
    bobs = sign_payload(w.ids["bob"], body)
    assert request_shape(bobs) is None   # well-formed and properly signed by Bob...
    assert open_input(bobs, w.did["p"], w.keys["p"]) == (None, "the sealed input does not open")  # ...but dead


def test_clear_input_still_works_when_asked_for(w):
    req = make_request(w.ids["alice"], ["m/a"], SECRET, clock=clock)
    assert sealed_to(req) is None and open_input(req, w.did["p"], None) == (SECRET, None)


def test_the_envelope_shape_is_enforced(w):
    req = w.sealed()
    sealed = req["sealed"]
    bad = {
        "both": {**req, "input": SECRET},
        "neither": {k: v for k, v in req.items() if k != "sealed"},
        "short nonce": {**req, "sealed": {**sealed, "nonce": base64.b64encode(b"x" * 12).decode()}},
        "not base64": {**req, "sealed": {**sealed, "ct": "!!!"}},
        "extra field": {**req, "sealed": {**sealed, "alg": "none"}},
        "unsorted": {**req, "sealed": {**sealed, "to": list(reversed(sealed["to"]))}},
        "short wrap": {**req, "sealed": {**sealed, "to": [[sealed["to"][0][0], base64.b64encode(b"k" * 32).decode()]]}},
        "no one": {**req, "sealed": {**sealed, "to": []}},
    }
    for why, obj in bad.items():
        assert request_shape(obj) is not None, why


def test_a_pin_must_be_able_to_open_it(w):
    with pytest.raises(ValueError, match="cannot open"):
        w.sealed(to=("p",), pins=[(w.did["q"], "m/a")])
    assert request_shape(w.sealed(to=("p", "q"), pins=[(w.did["q"], "m/a")])) is None


def test_bad_recipients_are_refused_at_sealing(w):
    with pytest.raises(ValueError):
        make_request(w.ids["alice"], ["m/a"], SECRET, seal_to=[], clock=clock)
    with pytest.raises(ValueError):
        make_request(w.ids["alice"], ["m/a"], SECRET, seal_to=[(w.did["p"], "AAAA")], clock=clock)
    with pytest.raises(ValueError):  # one DID, two keys
        make_request(w.ids["alice"], ["m/a"], SECRET, clock=clock,
                     seal_to=[(w.did["p"], b64(w.keys["p"])), (w.did["p"], b64(w.keys["q"]))])
    many = [(Identity.generate().did, b64(PrivateKey.generate())) for _ in range(MAX_RECIPIENTS + 1)]
    with pytest.raises(ValueError):
        make_request(w.ids["alice"], ["m/a"], SECRET, seal_to=many, clock=clock)


def test_sealed_input_is_capped_in_bytes(w):
    assert w.sealed() and make_request(w.ids["alice"], ["m/a"], "a" * MAX_INPUT, seal_to=w.to("p"), clock=clock)
    assert make_request(w.ids["alice"], ["m/a"], "中" * 6666, seal_to=w.to("p"), clock=clock)   # 19,998 bytes
    with pytest.raises(ValueError, match="too long to seal"):
        make_request(w.ids["alice"], ["m/a"], "中" * 6667, seal_to=w.to("p"), clock=clock)
    assert make_request(w.ids["alice"], ["m/a"], "中" * MAX_INPUT, clock=clock)  # the clear path keeps its cap


def test_the_largest_sealed_request_fits_one_relayed_datagram():
    """MAX_SEALED_INPUT is derived from the transport's budget. Rebuild the worst
    case -- the most bytes, recipients, models, scopes, pins and the longest
    decision -- with the transport's own encoders, and hold it to the relay cap."""
    transport = pytest.importorskip("secdogie_transport")
    from secdogie_identity import GRANTABLE_SCOPES
    from secdogie_inference.protocol import MAX_HOPS, MAX_MODELS, MAX_QUORUM, MAX_SEALED_INPUT, make_decision
    from secdogie_transport import mux, udp
    from secdogie_transport.relay import MAX_INNER

    assert transport
    user, decider = Identity.generate(), Identity.generate()
    models = sorted("v" * 120 + f"/{i:06d}" for i in range(MAX_MODELS))
    providers = [Identity.generate() for _ in range(MAX_QUORUM)]
    to = [(p.did, b64(PrivateKey.generate())) for p in providers]
    to += [(Identity.generate().did, b64(PrivateKey.generate())) for _ in range(MAX_RECIPIENTS - MAX_QUORUM)]
    text = "中" * (MAX_SEALED_INPUT // 3)
    req = make_request(user, models, text, scopes=sorted(GRANTABLE_SCOPES), quorum=MAX_QUORUM,
                       pins=[(p.did, models[i]) for i, p in enumerate(providers)], seal_to=to)
    legs = [Leg(p.did, models[i], (decider.did, *[Identity.generate().did for _ in range(MAX_HOPS - 2)], p.did))
            for i, p in enumerate(providers)]
    dec = make_decision(decider, req, legs, mode="normal", epoch=10 ** 9, attempt=2)
    payload = frame("req", request=req, decision=dec, leg=2, hop=MAX_HOPS - 1)
    datagram = udp._encode_frame(decider, providers[0].did, mux.encode("inference/v1", payload), 2 ** 40)
    assert len(base64.b64encode(datagram)) <= MAX_INNER


def test_the_client_refuses_input_it_could_not_seal_before_sending_anything():
    arc = sim.Arc(entries=("S2",))
    frames = tap(arc)
    with pytest.raises(ValueError, match="too long to seal"):
        arc.U.ask([ALPHA], "中" * 7000)
    arc.net.run()
    assert not [f for f in frames if f[0] == arc.did["U"]]
    arc.U.seal = False
    assert arc.ask([ALPHA], "中" * 7000).status == OUTCOME_ACCEPTED  # in the clear it may go, if asked to


def test_one_crowded_model_does_not_crowd_the_other_out_of_the_recipients():
    """Nine nodes serve one model, one node the other: a quorum across both
    needs that one node among the recipients."""
    from secdogie_inference import UserClient

    user, broker = Identity.generate(), Identity.generate()
    crowd = [Identity.generate().did for _ in range(MAX_RECIPIENTS + 1)]
    lone = Identity.generate().did
    trust = TrustRoots(mesh=Allowlist(set(crowd) | {lone, broker.did}), users=Allowlist({user.did}),
                       deciders=Allowlist(set()), coordinators=Allowlist(set()), brokers=Allowlist({broker.did}),
                       operators=Allowlist(set()))

    class Quiet:
        def send(self, to, payload):
            return True

        def connect(self, did):
            pass

    client = UserClient(user, Quiet(), trust, entries=[], clock=clock)
    for did, model in [(d, "a/many") for d in crowd] + [(lone, "b/one")]:
        assert client.leases.add(make_lease(broker, did, model, key_version=1, canary=new_canary(),
                                            seal_key=b64(PrivateKey.generate()), clock=clock))
    chosen = [p for p, _k in client.seal_targets(["a/many", "b/one"], NOW)]
    assert len(chosen) == MAX_RECIPIENTS and lone in chosen


def test_a_lease_names_a_well_formed_seal_key(w):
    k = Identity.generate()
    plain = make_lease(k, w.did["p"], "m/a", key_version=1, canary=new_canary(), clock=clock)
    keyed = make_lease(k, w.did["p"], "m/a", key_version=1, canary=new_canary(), seal_key=b64(w.keys["p"]),
                       clock=clock)
    assert lease_shape(plain) is None and lease_shape(keyed) is None and "seal_key" not in plain
    with pytest.raises(ValueError, match="seal_key"):
        make_lease(k, w.did["p"], "m/a", key_version=1, canary=new_canary(), seal_key="AAAA", clock=clock)


# -- across the mesh ---------------------------------------------------------------------

def tap(arc):
    """Every frame any participant receives, as (src, dst, text)."""
    seen = []
    for did in arc.net.parts:
        arc.net.drop[did] = lambda src, f, dst=did: seen.append((src, dst, json.dumps(f))) or False
    return seen


def recording(log):
    def backend(model, text, secret):
        log.append(text)
        return f"summary of {len(text)} characters"  # an answer that does not echo its prompt
    return backend


def test_no_carrier_ever_sees_the_prompt():
    heard = []
    arc = sim.Arc(entries=("S2",), backends={"L": recording(heard), "R": recording(heard)})
    frames = tap(arc)
    out = arc.ask([ALPHA, BETA], SECRET, quorum=2)
    assert out.status == OUTCOME_ACCEPTED
    assert heard == [SECRET, SECRET]                      # both model nodes read it
    assert frames and not [f for f in frames if SECRET in f[2]]
    submitted = [json.loads(t) for s, _d, t in frames if s == arc.did["U"] and '"k": "submit"' in t]
    assert submitted and all("sealed" in f["request"] for f in submitted)


def test_the_tap_would_have_caught_a_prompt_in_the_clear():
    """The test above is only as good as its tap: with sealing off, it sees it."""
    arc = sim.Arc(entries=("S2",), backends={"L": recording([]), "R": recording([])})
    arc.U.seal = False
    frames = tap(arc)
    assert arc.ask([ALPHA], SECRET).status == OUTCOME_ACCEPTED
    readers = {arc.short(d) for _s, d, t in frames if SECRET in t}
    assert {"S2", "W1", "L"} <= readers  # the decider and a forwarder read it, not only the model node


def test_in_lockdown_the_prompt_is_sealed_to_exactly_the_pins():
    heard = []
    arc = sim.Arc(entries=("S2",), backends={"L": recording(heard), "R": recording(heard)})
    ev = {"reporter": arc.did["S2"], "proposal": {"legs": []}, "violation": "quorum-downgrade"}
    arc.S2.publish(make_incident(arc.ids["S2"], DECIDER_SELF_REPORTED, ev, clock=arc.net.clock))
    arc.net.run()
    frames = tap(arc)
    out = arc.ask([ALPHA, BETA], SECRET)
    assert (out.status, out.mode) == (OUTCOME_ACCEPTED, MODE_LOCKDOWN)
    req = next(json.loads(t)["request"] for s, _d, t in frames
               if s == arc.did["U"] and '"k": "submit"' in t)
    assert sealed_to(req) == {p for p, _m in req["pins"]} == {arc.did["L"], arc.did["R"]}
    assert not [f for f in frames if SECRET in f[2]] and heard == [SECRET, SECRET]


def test_a_decider_routes_only_to_model_nodes_that_can_open_it():
    arc = sim.Arc(entries=("S2",))
    r_key = next(lease["seal_key"] for p, _m, lease in arc.S2.leases.eligible([BETA], arc.net.clock())
                 if p == arc.did["R"])  # the broker-signed lease every participant sees
    # Either model would do, and L is nearer -- but only R can read this one.
    req = make_request(arc.ids["U"], [ALPHA, BETA], SECRET, seal_to=[(arc.did["R"], r_key)],
                       clock=arc.net.clock)
    arc.U.send(arc.did["S2"], "submit", request=req)
    arc.net.settle(3)
    legs = [leg for d in arc.S2.noted("decide") for leg in d["legs"]]
    assert legs and {provider for provider, _via, _n in legs} == {arc.did["R"][-8:]}


def test_a_decision_model_that_routes_where_the_input_cannot_open_is_not_signed():
    """The decision model is not trusted: one that ignores the view and sends a
    sealed request to a node that cannot read it is caught by the node's own
    self-check -- nothing is signed, and the node reports its decision model."""
    class ToL(Router):
        def plan(self, view, request, *, quorum, avoid=frozenset()):
            return [Leg(arc.did["L"], ALPHA, (arc.did["S2"], arc.did["W1"], arc.did["L"]))]

    arc = sim.Arc(entries=("S2",), policies={"S2": ToL()})
    r_key = next(lease["seal_key"] for p, _m, lease in arc.S2.leases.eligible([BETA], arc.net.clock())
                 if p == arc.did["R"])
    req = make_request(arc.ids["U"], [ALPHA, BETA], SECRET, seal_to=[(arc.did["R"], r_key)],
                       clock=arc.net.clock)
    arc.U.send(arc.did["S2"], "submit", request=req)
    arc.net.settle(2)
    assert arc.S2.noted("decide") == []                       # it signed nothing
    assert arc.C.ledger.is_latched(decider_target(arc.did["S2"]))


def test_in_lockdown_a_model_node_that_is_not_pinned_cannot_read_the_prompt():
    """Seal to the pins, not to every node that could have answered."""
    gamma = "vendor-c/gamma"
    arc = sim.Arc(entries=("S2",), backends={"L": recording([]), "R": recording([])})
    third = make_lease(arc.ids["K"], arc.did["N2"], gamma, key_version=1, canary=new_canary(),
                       seal_key=b64(PrivateKey.generate()), clock=arc.net.clock)
    ev = {"reporter": arc.did["S2"], "proposal": {"legs": []}, "violation": "quorum-downgrade"}
    arc.S2.publish(make_incident(arc.ids["S2"], DECIDER_SELF_REPORTED, ev, clock=arc.net.clock))
    arc.net.run()
    for _p, _m, lease in arc.S2.leases.eligible([ALPHA, BETA], arc.net.clock()):  # the real ones
        arc.U.leases.add(lease)
    assert arc.U.leases.add(third)  # a third node that could answer gamma
    assert arc.did["N2"] in {p for p, _ in arc.U.seal_targets([ALPHA, BETA, gamma], arc.net.clock())}

    frames = tap(arc)
    out = arc.ask([ALPHA, BETA, gamma], SECRET)
    assert (out.status, out.mode) == (OUTCOME_ACCEPTED, MODE_LOCKDOWN)
    req = next(json.loads(t)["request"] for s, _d, t in frames
               if s == arc.did["U"] and '"k": "submit"' in t)
    assert sealed_to(req) == {arc.did["L"], arc.did["R"]}    # not N2


def test_a_late_refusal_of_an_earlier_try_does_not_end_the_wait_for_a_directory():
    arc = sim.Arc(entries=("S2", "S3"), fanout=2)
    arc.net.drop[arc.did["S2"]] = lambda src, f: f["k"] == "submit"   # both entries go quiet
    arc.net.drop[arc.did["S3"]] = lambda src, f: f["k"] == "submit"
    for _p, _m, lease in arc.S2.leases.eligible([ALPHA], arc.net.clock()):
        arc.U.leases.add(lease)
    handle = arc.U.ask([ALPHA], SECRET)
    old = arc.U._asks[handle].rhash
    assert old and arc.U._asks[handle].awaiting == {arc.did["S2"], arc.did["S3"]}

    arc.U.leases = LeaseBook(arc.trust)                               # the leases are gone
    arc.net.drop[arc.did["U"]] = lambda src, f: f["k"] == "dir"       # and no directory comes yet
    arc.net.until(lambda: arc.U._asks[handle].waiting_dir, limit=30)
    for entry in ("S2", "S3"):                                        # every entry of the old try refuses
        arc.U.handle(arc.did[entry], frame("refused", request_hash=old, reason="too slow"))
    assert arc.U.outcome(handle) is None                              # still waiting for the directory


def test_with_no_model_node_to_seal_to_the_client_waits_then_refuses_never_clear():
    arc = sim.Arc(entries=("S2",))
    arc.U.leases = LeaseBook(arc.trust)                       # knows no leases...
    frames = tap(arc)
    arc.net.drop[arc.did["U"]] = lambda src, f: f["k"] == "dir"  # ...and learns none
    out = arc.ask([ALPHA], SECRET)
    assert (out.status, out.reasons) == (OUTCOME_REFUSED, ("no model node to seal the input to",))
    sent = [t for s, _d, t in frames if s == arc.did["U"]]
    assert not [t for t in sent if '"k": "submit"' in t] and not [t for t in sent if SECRET in t]


def test_a_directory_ends_the_wait():
    arc = sim.Arc(entries=("S2",))
    arc.U.leases = LeaseBook(arc.trust)
    out = arc.ask([ALPHA], SECRET)
    assert out.status == OUTCOME_ACCEPTED and arc.U.leases.eligible([ALPHA], arc.net.clock())


def test_untrusted_roots_do_not_matter_to_the_envelope():
    """Opening reads no trust roots: who may *submit* is the request check's job."""
    w = World()
    TrustRoots(mesh=Allowlist(set()), users=Allowlist(set()), deciders=Allowlist(set()),
               coordinators=Allowlist(set()), brokers=Allowlist(set()), operators=Allowlist(set()))
    assert open_input(w.sealed(), w.did["p"], w.keys["p"]) == (SECRET, None)
