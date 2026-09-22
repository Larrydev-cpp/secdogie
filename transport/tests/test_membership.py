"""Tests for membership + endpoint gossip (P2P.3).

Pure/headless: self-signed records, last-writer-wins merge, anti-entropy
convergence across views, and the anti-forgery property (a relaying peer cannot
invent or alter another node's record)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity, sign_payload  # noqa: E402
from secdogie_transport import Endpoint, MembershipView, gossip_round  # noqa: E402
from secdogie_transport.membership import RECORD_TYPE, sign_record, verify_record  # noqa: E402


def _id_on(allow):
    ident = Identity.generate()
    allow.add(ident.did)
    return ident


# --- sign / verify ----------------------------------------------------------


def test_sign_and_verify_record_roundtrip():
    allow = Allowlist()
    me = _id_on(allow)
    signed = sign_record(me, [Endpoint("observed", "203.0.113.9", 5000)], last_seen=100.0)
    rec = verify_record(signed, allowlist=allow)
    assert rec is not None
    assert rec.did == me.did and rec.last_seen == 100.0
    assert (rec.endpoints.best().host, rec.endpoints.best().port) == ("203.0.113.9", 5000)


def test_verify_rejects_unauthorized_did():
    allow = Allowlist()
    stranger = Identity.generate()  # NOT on the allowlist
    signed = sign_record(stranger, [Endpoint("local", "10.0.0.1", 1)], last_seen=1.0)
    assert verify_record(signed, allowlist=allow) is None


def test_verify_rejects_tampered_endpoints():
    allow = Allowlist()
    me = _id_on(allow)
    signed = sign_record(me, [Endpoint("local", "10.0.0.1", 1)], last_seen=1.0)
    signed["endpoints"] = [{"kind": "public", "host": "6.6.6.6", "port": 66}]  # tamper after signing
    assert verify_record(signed, allowlist=allow) is None


def test_verify_rejects_did_signer_mismatch():
    allow = Allowlist()
    a, b = _id_on(allow), _id_on(allow)
    # b signs a record that CLAIMS to be a's did -> signer != did, refused
    forged = sign_payload(b, {"type": RECORD_TYPE, "did": a.did, "endpoints": [], "last_seen": 9.0})
    assert verify_record(forged, allowlist=allow) is None


def test_verify_rejects_far_future_timestamp():
    allow = Allowlist()
    me = _id_on(allow)
    signed = sign_record(me, [], last_seen=1_000_000.0)
    assert verify_record(signed, allowlist=allow, now=100.0) is None  # way beyond skew


# --- merge (last-writer-wins) ----------------------------------------------


def test_merge_is_last_writer_wins_by_last_seen():
    allow = Allowlist()
    me = _id_on(allow)
    view = MembershipView(allowlist=allow)
    assert view.merge_record(sign_record(me, [Endpoint("local", "10.0.0.1", 1)], last_seen=5.0)) is True
    # a newer record wins
    assert view.merge_record(sign_record(me, [Endpoint("public", "1.2.3.4", 9)], last_seen=9.0)) is True
    assert view.endpoints_for(me.did).best().host == "1.2.3.4"
    # an older record is ignored
    assert view.merge_record(sign_record(me, [Endpoint("local", "10.0.0.9", 2)], last_seen=3.0)) is False
    assert view.endpoints_for(me.did).best().host == "1.2.3.4"
    # a tie is ignored too
    assert view.merge_record(sign_record(me, [Endpoint("local", "10.0.0.9", 2)], last_seen=9.0)) is False


# --- gossip convergence -----------------------------------------------------


def test_gossip_round_converges_two_views():
    allow = Allowlist()
    a_id, b_id = _id_on(allow), _id_on(allow)
    va, vb = MembershipView(allowlist=allow), MembershipView(allowlist=allow)
    va.merge_record(sign_record(a_id, [Endpoint("observed", "a.host", 1)], last_seen=1.0))
    vb.merge_record(sign_record(b_id, [Endpoint("observed", "b.host", 2)], last_seen=1.0))
    into_a, into_b = gossip_round(va, vb)
    assert into_a == 1 and into_b == 1  # each learned the other's node
    assert va.known() == vb.known() == sorted([a_id.did, b_id.did])
    # idempotent: a second round with nothing new merges nothing
    assert gossip_round(va, vb) == (0, 0)


def test_gossip_relays_a_third_party_record_verifiably():
    # A knows C's self-signed record; B does not. After A<->B gossip, B has C's
    # record AND can verify C's own signature -- transitive trust, not A vouching.
    allow = Allowlist()
    a_id, c_id = _id_on(allow), _id_on(allow)
    c_signed = sign_record(c_id, [Endpoint("public", "c.host", 3)], last_seen=7.0)
    va = MembershipView(allowlist=allow)
    va.merge_record(sign_record(a_id, [Endpoint("local", "a", 1)], last_seen=1.0))
    va.merge_record(c_signed)  # A learned C somehow (e.g. rendezvous)
    vb = MembershipView(allowlist=allow)
    gossip_round(va, vb)
    assert c_id.did in vb.known()
    # what B stored is C's own signed blob, which verifies against C's key
    assert verify_record(vb.get(c_id.did).signed, allowlist=allow) is not None


def test_gossip_cannot_inject_a_forged_peer():
    # B tries to gossip a record for C's DID but signs it with B's key. A must
    # reject it -- a relayer cannot forge a peer it doesn't hold the key for.
    allow = Allowlist()
    b_id, c_id = _id_on(allow), _id_on(allow)
    va = MembershipView(allowlist=allow)
    vb = MembershipView(allowlist=allow)
    forged = sign_payload(
        b_id, {"type": RECORD_TYPE, "did": c_id.did,
                "endpoints": [{"kind": "public", "host": "evil", "port": 1}], "last_seen": 99.0}
    )
    assert vb.merge_record(forged) is False  # B's own view refuses the forgery
    # even if B hands the raw blob to A, A refuses it
    assert va.apply_records([forged]) == 0
    assert c_id.did not in va.known()


def test_three_views_converge_to_the_union():
    allow = Allowlist()
    ids = [_id_on(allow) for _ in range(3)]
    views = [MembershipView(allowlist=allow) for _ in range(3)]
    for v, ident in zip(views, ids, strict=True):
        v.merge_record(sign_record(ident, [Endpoint("observed", f"h{ident.did[-3:]}", 1)], last_seen=1.0))
    # gossip around the ring a couple of times
    for _ in range(2):
        gossip_round(views[0], views[1])
        gossip_round(views[1], views[2])
        gossip_round(views[2], views[0])
    everyone = sorted(i.did for i in ids)
    for v in views:
        assert v.known() == everyone


def test_records_for_and_digest_shapes():
    allow = Allowlist()
    me = _id_on(allow)
    view = MembershipView(allowlist=allow)
    view.merge_record(sign_record(me, [Endpoint("local", "10.0.0.1", 1)], last_seen=4.0))
    assert view.digest() == {me.did: 4.0}
    # a remote that already has me at a newer stamp is owed nothing
    assert view.records_for({me.did: 5.0}) == []
    # a remote that lacks me is owed my signed record
    owed = view.records_for({})
    assert len(owed) == 1 and json.loads(json.dumps(owed[0]))["did"] == me.did
