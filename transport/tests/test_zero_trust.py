"""Zero trust by default (Wave B): every transport entry point that decides whom
to hear refuses to start without an allowlist. ``None`` is never read as
"anyone"; ``ALLOW_ANY`` says so on purpose."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_identity import ALLOW_ANY, Allowlist, Identity  # noqa: E402
from secdogie_transport import (  # noqa: E402
    DirectUDPTransport,
    HubTransport,
    MembershipView,
    RendezvousServer,
    RoutingTable,
    UDPChannel,
    find_node,
    find_peer,
)


def test_direct_udp_transport_needs_an_allowlist():
    ch = UDPChannel("127.0.0.1", 0)
    try:
        with pytest.raises(ValueError, match="DirectUDPTransport"):
            DirectUDPTransport(Identity.generate(), ch)
    finally:
        ch.close()


@pytest.mark.parametrize("make, what", [
    (lambda: HubTransport(), "HubTransport"),
    (lambda: MembershipView(), "MembershipView"),
    (lambda: RendezvousServer(Identity.generate()), "RendezvousServer"),
    (lambda: RoutingTable(Identity.generate().did).add_signed({"type": "x"}), "add_signed"),
    (lambda: find_node(1, seed=[], query=lambda did, t: []), "DHT lookup"),
    (lambda: find_peer(Identity.generate().did, seed=[], query=lambda did, t: []), "DHT lookup"),
])
def test_every_entry_point_refuses_none(make, what):
    with pytest.raises(ValueError, match=what):
        make()


def test_allow_any_is_explicit_and_truthy():
    assert ALLOW_ANY.contains("did:key:zAnyone") and "did:key:zAnyone" in ALLOW_ANY
    assert bool(ALLOW_ANY) and repr(ALLOW_ANY) == "ALLOW_ANY"
    assert HubTransport(allowlist=ALLOW_ANY) is not None
    empty = Allowlist(set())
    assert not empty.contains("did:key:zAnyone")  # an empty list trusts no one, it is not "unset"
    assert MembershipView(allowlist=empty) is not None
