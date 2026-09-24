"""Kademlia routing + iterative lookup (P2P.4).

Headless / in-process: XOR-distance math, the k-bucket routing table (ordering,
bucket cap, authenticity gate), and iterative ``find_node``/``find_peer`` over an
in-proc mesh whose ``query`` returns each node's closest self-signed records --
full-knowledge (matches brute force), multi-hop chain, and ring traversal.
"""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_transport import (  # noqa: E402
    Endpoint,
    RoutingTable,
    find_node,
    find_peer,
    node_id,
    xor_distance,
)
from secdogie_transport.dht import bucket_index  # noqa: E402
from secdogie_transport.membership import sign_record  # noqa: E402


def _mk(allow, *, last_seen=1.0):
    """A fresh authorized identity + its self-signed record."""
    ident = Identity.generate()
    allow.add(ident.did)
    signed = sign_record(ident, [Endpoint("observed", f"h{ident.did[-4:]}", 5000)], last_seen=last_seen)
    return ident, signed


def _make_query(tables, self_records, *, k=20):
    """A query over an in-proc mesh: a node answers with its own record plus the
    records it holds closest to the target id."""
    def query(to_did, target_id):
        t = tables[to_did]
        out = [self_records[to_did]]
        out += [t.get(d).signed for d in t.closest(target_id, k)]
        return out

    return query


# --- distance / bucket math -------------------------------------------------


def test_node_id_is_deterministic_and_distance_symmetric():
    assert node_id("did:key:zX") == node_id("did:key:zX")
    a, b = node_id("did:key:zA"), node_id("did:key:zB")
    assert xor_distance(5, 5) == 0
    assert xor_distance(a, b) == xor_distance(b, a)


def test_bucket_index():
    a = node_id("did:key:zA")
    assert bucket_index(a, a) == -1          # self: never stored
    assert bucket_index(0b1000, 0b1001) == 0  # differ in the low bit
    assert bucket_index(0b0000, 0b1000) == 3  # differ in bit 3


# --- routing table ----------------------------------------------------------


def test_closest_orders_by_xor_distance():
    allow = Allowlist()
    me, _ = _mk(allow)
    t = RoutingTable(me.did, k=20)
    peers = [_mk(allow) for _ in range(8)]
    for _ident, signed in peers:
        assert t.add_signed(signed, allowlist=allow)
    target = node_id(peers[3][0].did)
    got = t.closest(target, 4)
    want = sorted((p[0].did for p in peers), key=lambda d: node_id(d) ^ target)[:4]
    assert got == want
    assert got[0] == peers[3][0].did  # a node is closest to its own id


def test_no_bucket_exceeds_k():
    allow = Allowlist()
    me, _ = _mk(allow)
    t = RoutingTable(me.did, k=3)
    for _ in range(60):
        _ident, signed = _mk(allow)
        t.add_signed(signed, allowlist=allow)
    assert all(len(b) <= 3 for b in t._buckets.values())


def test_add_signed_rejects_unauthorized_and_tampered():
    allow = Allowlist()
    me, _ = _mk(allow)
    t = RoutingTable(me.did)
    # a stranger not on the allowlist
    stranger = Identity.generate()
    s = sign_record(stranger, [Endpoint("local", "10.0.0.1", 1)], last_seen=1.0)
    assert t.add_signed(s, allowlist=allow) is False
    # a tampered record (endpoints changed after signing)
    _gid, good = _mk(allow)
    tampered = {**good, "endpoints": [{"kind": "public", "host": "evil", "port": 66}]}
    assert t.add_signed(tampered, allowlist=allow) is False
    assert len(t) == 0


def test_add_ignores_older_and_refreshes_on_newer():
    allow = Allowlist()
    me, _ = _mk(allow)
    ident = Identity.generate()
    allow.add(ident.did)
    t = RoutingTable(me.did)
    fresh = sign_record(ident, [Endpoint("public", "1.2.3.4", 9)], last_seen=9.0)
    old = sign_record(ident, [Endpoint("local", "10.0.0.1", 1)], last_seen=3.0)
    assert t.add_signed(fresh, allowlist=allow) is True
    assert t.add_signed(old, allowlist=allow) is False   # older -> ignored
    assert t.get(ident.did).last_seen == 9.0


# --- iterative lookup -------------------------------------------------------


def test_find_node_full_knowledge_matches_bruteforce():
    allow = Allowlist()
    nodes = [_mk(allow) for _ in range(16)]
    dids = [i.did for i, _ in nodes]
    self_records = {i.did: s for i, s in nodes}
    tables = {}
    for i, _ in nodes:
        t = RoutingTable(i.did, k=20)
        for j, sj in nodes:
            if j.did != i.did:
                t.add_signed(sj, allowlist=allow)
        tables[i.did] = t
    query = _make_query(tables, self_records)

    tid = node_id(dids[7])
    got = find_node(tid, seed=[dids[0]], query=query, allowlist=allow, count=5)
    want = sorted(dids, key=lambda d: node_id(d) ^ tid)[:5]
    assert got == want


def test_find_peer_traverses_multiple_hops():
    # A knows only B; B knows only C; C knows only T. A must hop A->B->C->T.
    allow = Allowlist()
    a, b, c, tt = (_mk(allow) for _ in range(4))
    ta = RoutingTable(a[0].did)
    tb = RoutingTable(b[0].did)
    tc = RoutingTable(c[0].did)
    ta.add_signed(b[1], allowlist=allow)   # A knows B
    tb.add_signed(c[1], allowlist=allow)   # B knows C
    tc.add_signed(tt[1], allowlist=allow)  # C knows T
    tables = {a[0].did: ta, b[0].did: tb, c[0].did: tc, tt[0].did: RoutingTable(tt[0].did)}
    self_records = {x[0].did: x[1] for x in (a, b, c, tt)}
    query = _make_query(tables, self_records)

    rec = find_peer(tt[0].did, seed=[b[0].did], query=query, allowlist=allow)
    assert rec is not None and rec.did == tt[0].did
    # an unknown DID is not discovered
    assert find_peer("did:key:zNoSuchPeer", seed=[b[0].did], query=query, allowlist=allow) is None


def test_ring_find_node_discovers_target():
    allow = Allowlist()
    nodes = [_mk(allow) for _ in range(12)]
    dids = [i.did for i, _ in nodes]
    self_records = {i.did: s for i, s in nodes}
    tables = {}
    for idx, (i, _s) in enumerate(nodes):
        t = RoutingTable(i.did, k=20)
        t.add_signed(nodes[(idx + 1) % len(nodes)][1], allowlist=allow)  # knows only the next node
        tables[i.did] = t
    query = _make_query(tables, self_records)

    target = dids[6]
    got = find_node(node_id(target), seed=[dids[0]], query=query, allowlist=allow, count=12)
    assert target in got  # reached by hopping around the ring


def test_find_node_is_bounded_and_idempotent():
    allow = Allowlist()
    nodes = [_mk(allow) for _ in range(6)]
    dids = [i.did for i, _ in nodes]
    self_records = {i.did: s for i, s in nodes}
    tables = {}
    for i, _ in nodes:
        t = RoutingTable(i.did)
        for j, sj in nodes:
            if j.did != i.did:
                t.add_signed(sj, allowlist=allow)
        tables[i.did] = t
    query = _make_query(tables, self_records)
    tid = node_id(dids[2])
    first = find_node(tid, seed=[dids[0]], query=query, allowlist=allow, count=6)
    second = find_node(tid, seed=[dids[0]], query=query, allowlist=allow, count=6)
    assert first == second  # deterministic, terminates
