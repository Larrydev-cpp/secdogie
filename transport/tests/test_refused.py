"""``DirectUDPTransport.on_refused``: the node hears about an authentic frame from
a DID it does not trust (so it can tell a revoked node so), and about nothing
else -- not a forgery, not a replay from a trusted peer, not someone else's
frame. The refused frame itself is still dropped."""
from __future__ import annotations

import json
import queue
import time

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import DirectUDPTransport, Endpoint, PeerIdentity, Session, UDPChannel  # noqa: E402


class Peer:
    def __init__(self, identity, trusts):
        self.identity = identity
        self.channel = UDPChannel("127.0.0.1", 0)
        self.transport = DirectUDPTransport(identity, self.channel, allowlist=Allowlist(trusts))
        self.inbox: queue.Queue = queue.Queue()
        me = Session("s", PeerIdentity(identity.did, ""), active=Endpoint("local", *self.channel.address))
        self.transport.register(me, lambda frm, msg: self.inbox.put((frm, msg)))


@pytest.fixture
def pair():
    a_id, b_id, c_id = Identity.generate(), Identity.generate(), Identity.generate()
    a = Peer(a_id, {c_id.did})  # a trusts only c
    b = Peer(b_id, {a_id.did})  # b is not trusted by a
    c = Peer(c_id, {a_id.did})
    refused: queue.Queue = queue.Queue()
    a.transport.on_refused(lambda signer, addr: refused.put((signer, addr)))
    for p in (b, c):
        p.transport.set_peer_endpoint(a_id.did, *a.channel.address)
    try:
        yield a, b, c, refused
    finally:
        for p in (a, b, c):
            p.channel.close()


def test_an_authentic_frame_from_an_untrusted_did_is_reported_and_dropped(pair):
    a, b, _, refused = pair
    assert b.transport.route(b.identity.did, a.identity.did, b"let me in")
    signer, addr = refused.get(timeout=3)
    assert signer == b.identity.did and addr[1] == b.channel.address[1]
    assert a.inbox.empty()  # still dropped


def test_forgeries_replays_and_other_peoples_frames_are_not_reported(pair):
    a, b, c, refused = pair
    frame = b.transport.build_frame(a.identity.did, b"x")
    forged = json.loads(frame)
    forged["data"] = "AAAA"  # edited after signing
    b.channel.send(*a.channel.address, json.dumps(forged).encode())
    not_ours = b.transport.build_frame(c.identity.did, b"x")  # addressed to c, delivered to a
    b.channel.send(*a.channel.address, not_ours)
    trusted = c.transport.build_frame(a.identity.did, b"once")
    c.channel.send(*a.channel.address, trusted)
    assert a.inbox.get(timeout=3) == (c.identity.did, b"once")
    c.channel.send(*a.channel.address, trusted)  # replayed: dropped, but c is trusted
    time.sleep(0.3)
    assert refused.empty()


def test_a_failing_handler_does_not_stop_the_transport(pair):
    a, b, c, _ = pair
    a.transport.on_refused(lambda signer, addr: 1 / 0)
    assert b.transport.route(b.identity.did, a.identity.did, b"knock")
    time.sleep(0.2)
    assert c.transport.route(c.identity.did, a.identity.did, b"still here")
    assert a.inbox.get(timeout=3) == (c.identity.did, b"still here")
