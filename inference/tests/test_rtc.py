"""The WebRTC carrier: DID ↔ DTLS binding, trust and admission at the handshake,
one connection per pair, and an inference request across DataChannels."""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("nacl")
pytest.importorskip("aiortc")
pytest.importorskip("websockets")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity, create_binding  # noqa: E402
from secdogie_inference import (  # noqa: E402
    OUTCOME_ACCEPTED,
    Config,
    KeyBroker,
    MemoryKeySource,
    MeshNode,
    TrustRoots,
    UserClient,
    assignments_from_bindings,
    make_admission,
)
from secdogie_inference.protocol import ROLE_FORWARD, ROLE_MODEL, ROLE_SERVE  # noqa: E402
from secdogie_inference.rtc import (  # noqa: E402
    RTC_HELLO,
    FailoverWire,
    RtcWire,
    check_hello,
    lobby_room,
    make_hello,
)
from signaling_stub import SignalingStub  # noqa: E402
from sim import echo, public_key_b64  # noqa: E402

FP_A = "sha-256 " + ":".join(["AA"] * 32)
FP_B = "sha-256 " + ":".join(["BB"] * 32)


def roots(mesh=(), users=(), operators=(), deciders=(), brokers=()):
    return TrustRoots(mesh=Allowlist(set(mesh)), users=Allowlist(set(users)), deciders=Allowlist(set(deciders)),
                      coordinators=Allowlist(), brokers=Allowlist(set(brokers)), operators=Allowlist(set(operators)))


def wait(cond, timeout=20.0):
    deadline = time.time() + timeout
    while not cond():
        assert time.time() < deadline, "condition never held"
        time.sleep(0.05)


class Sink:
    """A participant stand-in that records inbound frames."""

    def __init__(self):
        self.got = []

    def handle(self, did, payload):
        self.got.append((did, payload))


@pytest.fixture
def signaling():
    stub = SignalingStub()
    yield stub
    stub.close()


# -- the hello ------------------------------------------------------------------------------

def test_a_hello_binds_the_did_to_this_connections_fingerprints():
    a, b, op = Identity.generate(), Identity.generate(), Identity.generate()
    trust = roots(mesh={a.did, b.did}, operators={op.did})
    room = lobby_room(a.did)
    now = time.time()
    hello = make_hello(b, room=room, local_fp=FP_B, remote_fp=FP_A)
    assert hello["type"] == RTC_HELLO
    assert check_hello(hello, trust, room=room, my_fp=FP_A, peer_fp=FP_B, now=now) == (b.did, None)
    for kw, why in (({"my_fp": FP_B, "peer_fp": FP_A}, "fingerprints"),   # a gateway in the middle
                    ({"room": lobby_room(b.did)}, "another room"),
                    ({"now": now + 1000}, "stale")):
        args = {"room": room, "my_fp": FP_A, "peer_fp": FP_B, "now": now, **kw}
        with pytest.raises(ValueError, match=why):
            check_hello(hello, trust, **args)
    forged = {**hello, "did": a.did}
    with pytest.raises(ValueError, match="signature"):
        check_hello(forged, trust, room=room, my_fp=FP_A, peer_fp=FP_B, now=now)
    stranger = Identity.generate()
    lone = make_hello(stranger, room=room, local_fp=FP_B, remote_fp=FP_A)
    with pytest.raises(ValueError, match="not trusted"):
        check_hello(lone, trust, room=room, my_fp=FP_A, peer_fp=FP_B, now=now)
    admitted = make_hello(stranger, room=room, local_fp=FP_B, remote_fp=FP_A,
                          admission=make_admission(op, stranger.did))
    did, admission = check_hello(admitted, trust, room=room, my_fp=FP_A, peer_fp=FP_B, now=now)
    assert did == stranger.did and admission["subject"] == stranger.did
    someone_elses = make_hello(stranger, room=room, local_fp=FP_B, remote_fp=FP_A,
                               admission=make_admission(op, b.did))
    with pytest.raises(ValueError, match="not trusted"):
        check_hello(someone_elses, trust, room=room, my_fp=FP_A, peer_fp=FP_B, now=now)


# -- over real DataChannels -----------------------------------------------------------------

def test_two_nodes_bind_over_a_datachannel_and_exchange_frames(signaling):
    a, b = Identity.generate(), Identity.generate()
    trust = roots(mesh={a.did, b.did})
    wa, wb = RtcWire(a, trust, signaling.url), RtcWire(b, trust, signaling.url, listen=False)
    sa, sb = Sink(), Sink()
    wa.attach(sa)
    wb.attach(sb)
    try:
        assert not wb.send(a.did, b"too early")
        wb.connect(a.did)
        wait(lambda: a.did in wb.peers() and b.did in wa.peers())
        assert wb.send(a.did, b'{"k":"flood"}') and wa.send(b.did, b"back")
        wait(lambda: sa.got and sb.got)
        assert sa.got == [(b.did, b'{"k":"flood"}')] and sb.got == [(a.did, b"back")]
    finally:
        wa.close()
        wb.close()


def test_an_untrusted_peer_is_refused_and_an_admitted_one_accepted(signaling):
    a, b, op = Identity.generate(), Identity.generate(), Identity.generate()
    accepted = []
    a_trust = roots(mesh={a.did}, operators={op.did})        # a does not know b
    b_trust = roots(mesh={a.did, b.did}, operators={op.did})
    wa = RtcWire(a, a_trust, signaling.url, on_admission=lambda adm: accepted.append(adm) or True)
    wb = RtcWire(b, b_trust, signaling.url, listen=False)
    try:
        wb.connect(a.did)
        time.sleep(4)
        assert b.did not in wa.peers()
        wb.close()
        wb = RtcWire(b, b_trust, signaling.url, listen=False, admission=make_admission(op, b.did))
        wb.connect(a.did)
        wait(lambda: b.did in wa.peers())
        assert [x["subject"] for x in accepted] == [b.did]
    finally:
        wa.close()
        wb.close()


def test_simultaneous_dials_settle_on_one_connection(signaling):
    a, b = Identity.generate(), Identity.generate()
    trust = roots(mesh={a.did, b.did})
    wa, wb = RtcWire(a, trust, signaling.url), RtcWire(b, trust, signaling.url)
    sa, sb = Sink(), Sink()
    wa.attach(sa)
    wb.attach(sb)
    try:
        wa.connect(b.did)
        wb.connect(a.did)
        wait(lambda: b.did in wa.peers() and a.did in wb.peers())
        time.sleep(1.0)
        assert wa._links[b.did].dialer == wb._links[a.did].dialer == min(a.did, b.did)
        assert wa.send(b.did, b"1") and wb.send(a.did, b"2")
        wait(lambda: sa.got and sb.got)
    finally:
        wa.close()
        wb.close()


def test_an_inference_request_crosses_webrtc_and_falls_back_to_nothing_else(signaling):
    ids = {n: Identity.generate() for n in ("U", "S", "P", "K")}
    did = {n: i.did for n, i in ids.items()}
    trust = roots(mesh={did["S"], did["P"], did["K"]}, users={did["U"]}, deciders={did["S"]},
                  brokers={did["K"]})
    live = Config(heartbeat_every=0.3, fail_after=3.0, leg_timeout=3.0, advise_every=1.0)
    wires = {n: RtcWire(ids[n], trust, signaling.url) for n in ids}

    class NoUdp:  # FailoverWire's UDP side: nothing, so every frame must cross WebRTC
        def attach(self, participant):
            pass

        def send(self, to, payload):
            return False

        def connect(self, did):
            pass

    wire = {n: FailoverWire(wires[n], NoUdp()) for n in ids}
    tkey = PrivateKey.generate()
    binding = create_binding(ids["P"], public_key_b64(tkey), key_version=1)
    parts = {
        "S": MeshNode(ids["S"], wire["S"], trust, roles={ROLE_SERVE, ROLE_FORWARD}, config=live),
        "P": MeshNode(ids["P"], wire["P"], trust, roles={ROLE_MODEL}, backend=echo, transport_key=tkey, config=live),
        "K": KeyBroker(ids["K"], wire["K"], trust, MemoryKeySource({"m/a": "sk-rtc"}),
                       assignments=assignments_from_bindings([binding], {did["P"]: ["m/a"]}, trust), config=live),
    }
    parts["U"] = UserClient(ids["U"], wire["U"], trust, entries=[did["S"]], config=live, timeout=8.0)
    for n, part in parts.items():
        wire[n].attach(part)
    for a, b in (("S", "P"), ("P", "K")):
        parts[a].connect(did[b])
    stop = threading.Event()

    def ticker():
        while not stop.is_set():
            for part in parts.values():
                part.tick()
            time.sleep(0.05)

    threading.Thread(target=ticker, daemon=True).start()
    try:
        wait(lambda: parts["S"].leases.eligible(["m/a"], time.time()), timeout=40)
        handle = parts["U"].ask(["m/a"], "over webrtc")
        wait(lambda: parts["U"].outcome(handle) is not None, timeout=30)
        out = parts["U"].outcome(handle)
        assert out.status == OUTCOME_ACCEPTED and out.output == "answer(over webrtc)"
        assert did["S"] in wires["U"].peers()
    finally:
        stop.set()
        for w in wires.values():
            w.close()
