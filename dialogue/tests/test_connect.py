"""``open_session``: the one wiring every App uses. It trusts exactly the node
it is given, needs somewhere to send, refuses a binding for another DID, and
closes what it opened -- also when opening fails half way."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_transport")

from secdogie_dialogue.connect import NodeNotFound, open_session  # noqa: E402
from secdogie_identity import Identity  # noqa: E402
from secdogie_transport import UDPChannel  # noqa: E402
from secdogie_transport import udp as udp_mod  # noqa: E402

APP, NODE, OTHER = (Identity.generate() for _ in range(3))


def test_it_trusts_exactly_the_node_given():
    link = open_session(APP, NODE.did, listen=("127.0.0.1", 0), node_addr=("127.0.0.1", 9), start=False)
    try:
        assert link.controller.peer_did == NODE.did
        assert link.transport._allowlist.dids() == {NODE.did}
        assert link.transport.peer_endpoint(NODE.did) == ("127.0.0.1", 9)
    finally:
        link.close()


def test_nowhere_to_send_is_refused_and_nothing_is_left_open(monkeypatch):
    opened = []
    real = udp_mod.UDPChannel.__init__

    def track(self, *a, **k):
        real(self, *a, **k)
        opened.append(self)

    monkeypatch.setattr(UDPChannel, "__init__", track)
    with pytest.raises(NodeNotFound):
        open_session(APP, NODE.did, listen=("127.0.0.1", 0))
    assert opened and all(ch._sock.fileno() == -1 for ch in opened)  # closed again


def test_a_binding_for_another_did_is_refused():
    from nacl.public import PrivateKey
    from secdogie_identity.binding import create_binding
    from secdogie_transport.sealed import public_key_b64

    other = create_binding(OTHER, public_key_b64(PrivateKey.generate()), key_version=1)
    with pytest.raises(ValueError, match="binding"):
        open_session(APP, NODE.did, listen=("127.0.0.1", 0), node_addr=("127.0.0.1", 9),
                     transport_key=PrivateKey.generate(), binding=other, start=False)


def test_close_closes_the_channel():
    link = open_session(APP, NODE.did, listen=("127.0.0.1", 0), node_addr=("127.0.0.1", 9))
    link.close()
    assert link.channel._sock.fileno() == -1


def test_a_node_not_registered_at_the_rendezvous_given_is_not_found():
    import time

    from secdogie_identity import Allowlist
    from secdogie_transport import DirectUDPTransport, Endpoint, RendezvousService
    from secdogie_transport.membership import ROLE_RENDEZVOUS, sign_record

    rv = Identity.generate()
    ch = UDPChannel("127.0.0.1", 0)
    try:
        trust = Allowlist({APP.did, NODE.did})
        RendezvousService(DirectUDPTransport(rv, ch, allowlist=trust), allowlist=trust)
        record = sign_record(rv, [Endpoint("local", *ch.address)], last_seen=time.time(), roles=[ROLE_RENDEZVOUS])
        with pytest.raises(NodeNotFound, match="not registered"):
            open_session(APP, NODE.did, listen=("127.0.0.1", 0), rendezvous=[record])
    finally:
        ch.close()
