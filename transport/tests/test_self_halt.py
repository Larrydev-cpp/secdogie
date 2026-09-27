"""Graceful halt on self-revocation (R1.2c), on real loopback UDP.

When the mesh's masters revoke a node's own DID, the node winds itself down --
stops serving, closes its socket -- the same as if its operator had stopped it.
The decision is the pure helper halt_on_self_revocation; here it is wired to a
node's TrustPolicy.on_change so an inbound gossiped revocation triggers it."""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("nacl")

from secdogie_identity import (
    Allowlist,
    Identity,
    TrustPolicy,
    cosign,
    create_revocation,
    halt_on_self_revocation,
)
from secdogie_identity.revocation import MasterSet
from secdogie_transport import (
    DirectUDPTransport,
    MembershipView,
    RelayService,
    RevocationGossip,
    UDPChannel,
)


def _wait(pred, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not pred():
        time.sleep(0.01)
    return pred()


def test_halt_helper_fires_only_for_self():
    ran = []
    others = {"did:key:zSomeoneElse"}
    assert halt_on_self_revocation(others, "did:key:zMe", [lambda: ran.append(1)]) is False
    assert ran == []
    assert halt_on_self_revocation({"did:key:zMe"}, "did:key:zMe", [lambda: ran.append(1)]) is True
    assert ran == [1]


def test_halt_helper_runs_every_action_even_if_one_raises():
    ran = []

    def boom():
        raise RuntimeError("stop step failed")

    fired = halt_on_self_revocation(
        {"did:key:zMe"}, "did:key:zMe",
        [lambda: ran.append("a"), boom, lambda: ran.append("b")],
    )
    assert fired is True and ran == ["a", "b"]  # boom did not abort the sequence


class HeadlessNode:
    """A relay-serving node that halts itself when its own DID is revoked."""

    def __init__(self, allow, masters):
        self.identity = Identity.generate()
        self.did = self.identity.did
        allow.add(self.did)
        self.policy = TrustPolicy(allow, masters=masters)
        self.channel = UDPChannel()
        self.transport = DirectUDPTransport(self.identity, self.channel, allowlist=self.policy)
        self.view = MembershipView(allowlist=self.policy)
        self.service = RelayService(self.transport, allowlist=self.policy)
        self.gossip = RevocationGossip(self.transport, self.policy, self.view)
        self.halted = threading.Event()
        # A node that finds its own DID revoked stops serving and closes its
        # socket, then signals the main loop to exit; the process exits 0.
        self.policy.on_change(lambda newly: halt_on_self_revocation(
            newly, self.did,
            [self.service.stop, self.channel.close, self.halted.set],
        ))

    def close(self):
        self.channel.close()


def test_a_node_halts_when_its_own_did_is_revoked():
    allow = Allowlist()
    master = Identity.generate()
    masters = MasterSet([master.did])
    node = None
    sender_channel = UDPChannel()
    try:
        node = HeadlessNode(allow, masters)
        # A revocation of somebody else does not touch it.
        node.policy.apply(cosign(master, create_revocation([Identity.generate().did])))
        assert not node.halted.is_set()

        # A revocation of its own DID, delivered over the gossip frame path,
        # trips the halt: it stops serving and closes its channel, exit 0 pending.
        rec = cosign(master, create_revocation([node.did]))
        import json
        frame = json.dumps({"t": "secdogie/revocation/gossip/v1", "record": rec}).encode()
        sender_channel.send(*node.channel.address, frame)

        assert _wait(lambda: node.halted.is_set())
        assert node.policy.is_revoked(node.did)
        # Serving has stopped and the socket is closed: no clients remain.
        assert node.service.clients() == []
    finally:
        sender_channel.close()
        if node is not None:
            node.close()
