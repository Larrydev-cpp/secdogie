from __future__ import annotations

import base64
import os

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity, create_binding  # noqa: E402
from secdogie_transport import (  # noqa: E402
    Endpoint,
    EndpointSet,
    HubTransport,
    PeerIdentity,
    Session,
)


def _tpk() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


def _peer(caps=()) -> tuple[Identity, str, PeerIdentity]:
    idn = Identity.generate()
    tpk = _tpk()
    binding = create_binding(idn, tpk, key_version=1, capabilities=caps)
    peer = PeerIdentity.from_binding(binding)
    return idn, tpk, peer


def test_peer_from_valid_binding():
    idn, tpk, peer = _peer(caps=("desktop.observe",))
    assert peer is not None
    assert peer.did == idn.did and peer.transport_public_key == tpk
    assert peer.has_capability("desktop.observe")


def test_peer_from_unauthorized_binding_is_none():
    idn = Identity.generate()
    binding = create_binding(idn, _tpk(), key_version=1)
    allow = Allowlist({Identity.generate().did})  # some other DID
    assert PeerIdentity.from_binding(binding, allowlist=allow) is None


def test_peer_from_expired_binding_is_none():
    idn = Identity.generate()
    binding = create_binding(idn, _tpk(), key_version=1, valid_from=0.0, expires_at=1.0)
    assert PeerIdentity.from_binding(binding, now=100.0) is None


def test_endpoint_set_prefers_public_then_observed():
    es = EndpointSet([Endpoint("candidate", "10.0.0.1", 5000), Endpoint("local", "192.168.1.2", 5000)])
    es.observe("203.0.113.7", 41000)  # observed
    assert es.best().kind == "observed"
    es.add(Endpoint("public", "198.51.100.9", 51820))
    assert es.best().kind == "public"


def test_endpoint_rejects_bad_kind_and_port():
    with pytest.raises(ValueError):
        Endpoint("weird", "1.2.3.4", 80)
    with pytest.raises(ValueError):
        Endpoint("public", "1.2.3.4", 0)


def test_session_migration_keeps_identity():
    _idn, _tpk_, peer = _peer()
    s = Session("sess-1", peer, EndpointSet([Endpoint("local", "10.0.0.5", 6000)]))
    assert s.active.host == "10.0.0.5"
    s.migrate(Endpoint("observed", "203.0.113.9", 7000))
    assert s.active.host == "203.0.113.9"
    assert s.session_id == "sess-1" and s.did == peer.did  # identity unchanged


def test_hub_routes_between_two_registered_peers():
    _a_id, _a_tpk, a = _peer()
    _b_id, _b_tpk, b = _peer()
    hub = HubTransport()
    inbox_b = []
    assert hub.register(Session("sa", a), lambda frm, msg: None)
    assert hub.register(Session("sb", b), lambda frm, msg: inbox_b.append((frm, msg)))
    assert hub.route(a.did, b.did, b"hello")
    assert inbox_b == [(a.did, b"hello")]


def test_hub_refuses_unauthorized_peer():
    _a_id, _a_tpk, a = _peer()
    hub = HubTransport(allowlist=Allowlist({Identity.generate().did}))
    assert hub.register(Session("sa", a), lambda frm, msg: None) is False
    assert a.did not in hub.peers()


def test_hub_route_fails_to_unknown_destination():
    _a_id, _a_tpk, a = _peer()
    hub = HubTransport()
    hub.register(Session("sa", a), lambda frm, msg: None)
    assert hub.route(a.did, "did:key:zNobody", b"x") is False


def test_hub_migration_keeps_delivery():
    _a_id, _a_tpk, a = _peer()
    _b_id, _b_tpk, b = _peer()
    hub = HubTransport()
    got = []
    hub.register(Session("sa", a), lambda frm, msg: None)
    hub.register(Session("sb", b, EndpointSet([Endpoint("local", "10.0.0.2", 6000)])), lambda frm, msg: got.append(msg))
    assert hub.migrate(b.did, Endpoint("observed", "203.0.113.4", 9000))  # b roamed
    assert hub.route(a.did, b.did, b"after-migration")
    assert got == [b"after-migration"]  # still delivered; session identity stable
