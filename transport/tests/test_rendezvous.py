"""Tests for rendezvous + reflexive endpoint discovery (P2P.1).

Pure-protocol tests drive the server/client frame by frame; the last test runs
two real nodes over UDPChannel on 127.0.0.1. Headless throughout."""
from __future__ import annotations

import json
import queue

import pytest

pytest.importorskip("nacl")

from secdogie_identity import (  # noqa: E402
    Allowlist,
    Identity,
    MasterSet,
    TrustPolicy,
    cosign,
    create_revocation,
    sign_payload,
)
from secdogie_transport import Endpoint, UDPChannel  # noqa: E402
from secdogie_transport.rendezvous import (  # noqa: E402
    LOOKUP_RESULT,
    REGISTER_ACK,
    RendezvousClient,
    RendezvousServer,
)


def _authorized():
    """A server + client whose DIDs are both on a shared allowlist."""
    allow = Allowlist()
    server_id = Identity.generate()
    client_id = Identity.generate()
    allow.add(server_id.did)
    allow.add(client_id.did)
    server = RendezvousServer(server_id, allowlist=allow)
    client = RendezvousClient(client_id, server_id.did)
    return allow, server_id, client_id, server, client


# --- register / reflexive discovery -----------------------------------------


def test_register_returns_the_reflexive_endpoint_the_server_observed():
    _, _, _, server, client = _authorized()
    frame = client.register_frame([Endpoint("local", "192.168.1.5", 40000)])
    ack = server.on_register(frame, src_addr=("203.0.113.9", 51000))  # what the server "sees"
    assert ack is not None
    reflexive = client.handle_register_ack(ack)
    assert reflexive == Endpoint("observed", "203.0.113.9", 51000)
    # the client now advertises both its local candidate and its learned public addr
    kinds = {e.kind for e in client.self_endpoints.all()}
    assert "observed" in kinds and "local" in kinds
    # best() prefers the observed (public) endpoint over the LAN one
    assert client.self_endpoints.best().kind == "observed"


def test_server_records_candidate_plus_reflexive_for_lookup():
    _, _, client_id, server, client = _authorized()
    server.on_register(
        client.register_frame([Endpoint("local", "10.0.0.2", 5000)]),
        src_addr=("198.51.100.7", 6000),
    )
    known = server.known(client_id.did)
    assert known is not None
    hosts = {(e.kind, e.host, e.port) for e in known.all()}
    assert ("local", "10.0.0.2", 5000) in hosts
    assert ("observed", "198.51.100.7", 6000) in hosts


# --- lookup -----------------------------------------------------------------


def test_lookup_returns_a_registered_peers_endpoints():
    allow = Allowlist()
    server_id, a_id, b_id = Identity.generate(), Identity.generate(), Identity.generate()
    for did in (server_id.did, a_id.did, b_id.did):
        allow.add(did)
    server = RendezvousServer(server_id, allowlist=allow)
    a = RendezvousClient(a_id, server_id.did)
    b = RendezvousClient(b_id, server_id.did)
    # b registers, then a looks b up
    server.on_register(b.register_frame([Endpoint("candidate", "b.example", 7000)]),
                       src_addr=("203.0.113.20", 7001))
    result = server.on_lookup(a.lookup_frame(b_id.did))
    assert result is not None
    target, endpoints = a.handle_lookup_result(result)
    assert target == b_id.did
    keys = {(e.host, e.port) for e in endpoints.all()}
    assert ("b.example", 7000) in keys and ("203.0.113.20", 7001) in keys


def test_lookup_of_unregistered_peer_is_empty():
    allow = Allowlist()
    server_id, a_id, ghost = Identity.generate(), Identity.generate(), Identity.generate()
    for did in (server_id.did, a_id.did, ghost.did):
        allow.add(did)
    server = RendezvousServer(server_id, allowlist=allow)
    a = RendezvousClient(a_id, server_id.did)
    result = server.on_lookup(a.lookup_frame(ghost.did))
    target, endpoints = a.handle_lookup_result(result)
    assert target == ghost.did and endpoints.all() == []


# --- authorization + integrity ----------------------------------------------


def test_unauthorized_did_register_is_dropped():
    allow = Allowlist()
    server_id = Identity.generate()
    allow.add(server_id.did)  # server authorizes only itself
    server = RendezvousServer(server_id, allowlist=allow)
    stranger = RendezvousClient(Identity.generate(), server_id.did)  # not on the allowlist
    assert server.on_register(stranger.register_frame([]), src_addr=("1.2.3.4", 9)) is None


def test_unauthorized_did_lookup_is_dropped():
    allow = Allowlist()
    server_id = Identity.generate()
    allow.add(server_id.did)
    server = RendezvousServer(server_id, allowlist=allow)
    stranger = RendezvousClient(Identity.generate(), server_id.did)
    assert server.on_lookup(stranger.lookup_frame(server_id.did)) is None


def test_tampered_register_frame_is_dropped():
    _, _, _, server, client = _authorized()
    frame = client.register_frame([Endpoint("local", "10.0.0.9", 8000)])
    obj = json.loads(frame)
    obj["did"] = Identity.generate().did  # claim a different DID than the signature covers
    tampered = json.dumps(obj).encode("utf-8")
    assert server.on_register(tampered, src_addr=("5.6.7.8", 10)) is None


def test_client_rejects_ack_not_from_the_pinned_server():
    _, server_id, client_id, server, client = _authorized()
    # A different identity forges a well-formed ack addressed to the client.
    imposter = Identity.generate()
    forged = sign_payload(
        imposter,
        {"type": REGISTER_ACK, "to": client_id.did,
         "reflexive": {"kind": "observed", "host": "6.6.6.6", "port": 66}, "ts": 0},
    )
    assert client.handle_register_ack(json.dumps(forged).encode("utf-8")) is None


def test_client_rejects_result_addressed_to_someone_else():
    _, server_id, _, server, client = _authorized()
    other = Identity.generate()
    result = sign_payload(
        server_id,
        {"type": LOOKUP_RESULT, "to": other.did, "target_did": "did:key:zX", "endpoints": [], "ts": 0},
    )
    assert client.handle_lookup_result(json.dumps(result).encode("utf-8")) is None


# --- real loopback ----------------------------------------------------------


def test_two_nodes_over_real_udp_discover_each_other():
    allow = Allowlist()
    server_id, a_id, b_id = Identity.generate(), Identity.generate(), Identity.generate()
    for did in (server_id.did, a_id.did, b_id.did):
        allow.add(did)
    server = RendezvousServer(server_id, allowlist=allow)
    srv_ch = UDPChannel()
    inbox: queue.Queue = queue.Queue()

    def serve(data, addr):
        # dispatch by message type; reply straight back to the source
        obj = json.loads(data)
        reply = server.on_register(data, addr) if obj.get("type", "").endswith("register/v1") \
            else server.on_lookup(data)
        if reply is not None:
            srv_ch.send(addr[0], addr[1], reply)

    srv_ch.start(serve)

    a = RendezvousClient(a_id, server_id.did)
    b = RendezvousClient(b_id, server_id.did)
    a_ch, b_ch = UDPChannel(), UDPChannel()
    a_ch.start(lambda d, _a: inbox.put(("a", d)))
    b_ch.start(lambda d, _a: inbox.put(("b", d)))
    try:
        shost, sport = srv_ch.address
        # b registers a candidate endpoint
        b_ch.send(shost, sport, b.register_frame([Endpoint("candidate", "127.0.0.1", b_ch.address[1])]))
        who, ack = inbox.get(timeout=2.0)
        assert who == "b" and b.handle_register_ack(ack) is not None
        # a looks b up and gets b's endpoints
        a_ch.send(shost, sport, a.lookup_frame(b_id.did))
        who, res = inbox.get(timeout=2.0)
        assert who == "a"
        target, endpoints = a.handle_lookup_result(res)
        assert target == b_id.did and endpoints.best() is not None
    finally:
        srv_ch.close()
        a_ch.close()
        b_ch.close()


# --- freshness: what a replayed or stale frame can no longer do (T3) -----------


class _Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _clocked(**server_kw):
    clock = _Clock()
    server_id, client_id = Identity.generate(), Identity.generate()
    allow = Allowlist({server_id.did, client_id.did})
    server = RendezvousServer(server_id, allowlist=allow, clock=clock, **server_kw)
    client = RendezvousClient(client_id, server_id.did, clock=clock)
    return clock, server, client, client_id


def test_a_replayed_register_cannot_move_the_reflexive_endpoint():
    _, server, client, client_id = _clocked()
    frame = client.register_frame([])
    assert server.on_register(frame, ("198.51.100.7", 4000)) is not None
    # an eavesdropper replays the captured frame from its own address
    assert server.on_register(frame, ("203.0.113.66", 5555)) is None
    assert server.known(client_id.did).best().host == "198.51.100.7"
    assert server.stats["dropped_replayed"] == 1


def test_a_register_outside_the_clock_window_is_dropped():
    clock, server, client, client_id = _clocked(max_skew=60)
    frame = client.register_frame([])
    clock.now += 61  # delivered (or replayed) a minute late
    assert server.on_register(frame, ("198.51.100.7", 4000)) is None
    assert server.known(client_id.did) is None
    assert server.stats["dropped_stale"] == 1


def test_requests_stay_fresh_when_the_clock_does_not_move():
    _, server, client, _ = _clocked()
    for _ in range(3):  # a frozen clock: the client still sends strictly newer ts
        assert server.on_register(client.register_frame([]), ("198.51.100.7", 4000)) is not None


def test_only_the_ack_to_the_latest_register_counts():
    _, server, client, _ = _clocked()
    old_ack = server.on_register(client.register_frame([]), ("198.51.100.7", 4000))
    new_ack = server.on_register(client.register_frame([]), ("198.51.100.7", 4001))
    assert client.handle_register_ack(old_ack) is None  # answers a register that was superseded
    assert client.handle_register_ack(new_ack).port == 4001
    assert client.handle_register_ack(new_ack) is None  # and only once


def test_a_lookup_result_is_accepted_once_and_only_for_the_target_asked():
    _, server, client, client_id = _clocked()
    server.on_register(client.register_frame([]), ("198.51.100.7", 4000))
    result = server.on_lookup(client.lookup_frame(client_id.did))
    assert client.handle_lookup_result(result)[1].best().port == 4000
    assert client.handle_lookup_result(result) is None  # replayed later: unsolicited


def test_an_unsolicited_lookup_result_is_rejected():
    clock, server, client, client_id = _clocked()
    other = RendezvousClient(client_id, server.did, clock=clock)  # same DID, separate request state
    result = server.on_lookup(other.lookup_frame(client_id.did))
    assert result is not None
    assert client.handle_lookup_result(result) is None  # this client asked nothing


def test_an_expired_registration_is_not_handed_out():
    clock, server, client, client_id = _clocked(ttl=90)
    server.on_register(client.register_frame([]), ("198.51.100.7", 4000))
    clock.now += 91
    assert server.known(client_id.did) is None
    _, endpoints = client.handle_lookup_result(server.on_lookup(client.lookup_frame(client_id.did)))
    assert endpoints.best() is None


def test_a_flooding_peer_is_rate_limited():
    _, server, client, _ = _clocked(rate=1, burst=3)
    answered = [server.on_register(client.register_frame([]), ("198.51.100.7", 4000)) for _ in range(5)]
    assert sum(a is not None for a in answered) == 3
    assert server.stats["dropped_rate_limited"] == 2


def test_a_peer_revoked_after_registering_is_no_longer_handed_out():
    master, server_id, a_id, b_id = (Identity.generate() for _ in range(4))
    policy = TrustPolicy(Allowlist({a_id.did, b_id.did}), masters=MasterSet([master.did]))
    server = RendezvousServer(server_id, allowlist=policy)
    a = RendezvousClient(a_id, server_id.did)
    b = RendezvousClient(b_id, server_id.did)
    server.on_register(a.register_frame([]), ("198.51.100.7", 4000))
    policy.apply(cosign(master, create_revocation([a_id.did])))
    _, endpoints = b.handle_lookup_result(server.on_lookup(b.lookup_frame(a_id.did)))
    assert endpoints.best() is None
