"""The inference mesh over the real transport: DirectUDPTransport + ChannelMux
on 127.0.0.1, receive threads and a tick loop driving the same participants.

    U (user) -- S (serving) -- P (model) -- K (key authority)
"""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_transport")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Allowlist, Identity, create_binding  # noqa: E402
from secdogie_inference import (  # noqa: E402
    OUTCOME_ACCEPTED,
    Config,
    KeyBroker,
    MemoryKeySource,
    MeshNode,
    MuxWire,
    TrustRoots,
    UserClient,
    assignments_from_bindings,
)
from secdogie_inference.protocol import ROLE_FORWARD, ROLE_MODEL, ROLE_SERVE  # noqa: E402
from secdogie_transport import (  # noqa: E402
    ChannelMux,
    DirectUDPTransport,
    Endpoint,
    PeerIdentity,
    Session,
    UDPChannel,
)
from sim import echo, public_key_b64  # noqa: E402

LIVE = Config(heartbeat_every=0.2, fail_after=2.0, leg_timeout=2.0, advise_every=1.0)


class Host:
    """One process's transport stack: a UDP socket, the direct transport, a mux."""

    def __init__(self, identity, allow, book):
        self.identity = identity
        self.channel = UDPChannel("127.0.0.1", 0)
        book[identity.did] = self.channel.address
        self.transport = DirectUDPTransport(identity, self.channel, allowlist=allow)
        me = Session("s", PeerIdentity(identity.did, ""), active=Endpoint("local", *self.channel.address))
        self.mux = ChannelMux(self.transport, me)
        self.wire = MuxWire(self.mux, resolve=lambda did: self.transport.set_peer_endpoint(did, *book[did]))

    def close(self):
        self.channel.close()


def test_a_request_crosses_real_udp_and_comes_back_verified():
    ids = {n: Identity.generate() for n in ("U", "S", "P", "K")}
    did = {n: i.did for n, i in ids.items()}
    trust = TrustRoots(mesh=Allowlist({did["S"], did["P"], did["K"]}), users=Allowlist({did["U"]}),
                       deciders=Allowlist({did["S"]}), coordinators=Allowlist(), brokers=Allowlist({did["K"]}),
                       operators=Allowlist())
    allow = Allowlist(set(did.values()))
    book: dict = {}
    hosts = {n: Host(ids[n], allow, book) for n in ids}
    tkey = PrivateKey.generate()
    binding = create_binding(ids["P"], public_key_b64(tkey), key_version=1)
    assignments = assignments_from_bindings([binding], {did["P"]: ["m/a"]}, trust)

    parts = {
        "S": MeshNode(ids["S"], hosts["S"].wire, trust, roles={ROLE_SERVE, ROLE_FORWARD}, config=LIVE),
        "P": MeshNode(ids["P"], hosts["P"].wire, trust, roles={ROLE_MODEL}, backend=echo, transport_key=tkey,
                      config=LIVE),
        "K": KeyBroker(ids["K"], hosts["K"].wire, trust, MemoryKeySource({"m/a": "sk-live"}),
                       assignments=assignments, config=LIVE),
    }
    parts["U"] = UserClient(ids["U"], hosts["U"].wire, trust, entries=[did["S"]], config=LIVE, timeout=5.0)
    for n, part in parts.items():
        hosts[n].wire.attach(part)
    for a, b in (("S", "P"), ("P", "K")):
        parts[a].connect(did[b])
        parts[b].connect(did[a])
    parts["S"].connect(did["U"])

    stop = threading.Event()

    def ticker():
        while not stop.is_set():
            for part in parts.values():
                part.tick()
            time.sleep(0.05)

    threading.Thread(target=ticker, daemon=True).start()
    try:
        deadline = time.time() + 10
        while not parts["S"].leases.eligible(["m/a"], time.time()) and time.time() < deadline:
            time.sleep(0.05)  # the lease reaches S inside P's heartbeat
        handle = parts["U"].ask(["m/a"], "over the wire")
        while parts["U"].outcome(handle) is None and time.time() < deadline:
            time.sleep(0.05)
        out = parts["U"].outcome(handle)
        assert out is not None and out.status == OUTCOME_ACCEPTED
        assert out.output == "answer(over the wire)" and out.providers == (did["P"],)
    finally:
        stop.set()
        for host in hosts.values():
            host.close()
