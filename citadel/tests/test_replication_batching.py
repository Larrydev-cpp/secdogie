"""Journal replication in datagram-sized batches (stage 3, mesh): each message
carries at most about ``max_bytes`` of events, authors are taken round robin
(so an author the peer refuses never crowds out the rest), each author's events
stay a gap-free prefix, and repeated rounds converge even over a link that drops,
duplicates and reorders messages."""
from __future__ import annotations

import json
import random

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.replication import ReplicationPeer  # noqa: E402
from secdogie_citadel.sync import events_batch, respond_to_have  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402

A, B, C = Identity.generate(), Identity.generate(), Identity.generate()


def _journal(identity, *trusted):
    return Journal(identity=identity, allowlist=Allowlist({d.did for d in trusted}))


def _fill(journal, n, tag):
    for i in range(n):
        journal.append("note", {"i": i, "tag": tag, "pad": "x" * 200})


def test_a_batch_is_capped_and_says_there_is_more():
    a = _journal(A, A)
    _fill(a, 20, "a")
    events, more = events_batch(a, {}, 1000)
    assert more and 1 <= len(events) < 20
    assert sum(len(json.dumps(e, separators=(",", ":"))) + 1 for e in events) <= 1000
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))  # a gap-free prefix
    rest, more = events_batch(a, {A.did: len(events)}, 10**9)
    assert not more and [e["seq"] for e in rest] == list(range(len(events) + 1, 21))


def test_a_batch_always_carries_something_when_something_is_due():
    a = _journal(A, A)
    _fill(a, 2, "a")
    events, more = events_batch(a, {}, 1)  # smaller than any one event
    assert len(events) == 1 and more


def test_authors_are_taken_round_robin():
    a = _journal(A, A, C)
    c = _journal(C, C)
    _fill(c, 10, "c")
    a.merge(c.events())
    _fill(a, 10, "a")
    events, _ = events_batch(a, {}, 2500)
    authors = [e["author"] for e in events]
    assert A.did in authors and C.did in authors  # neither author starves the other
    assert abs(authors.count(A.did) - authors.count(C.did)) <= 1


def test_uncapped_response_is_unchanged():
    a = _journal(A, A)
    _fill(a, 5, "a")
    msg = respond_to_have(a, {"kind": "journal_have", "heads": {}})
    assert len(msg["events"]) == 5 and "more" not in msg


class LossyLink:
    """Delivers messages between two peers, dropping, duplicating and reordering."""

    def __init__(self, seed, loss=0.2, dup=0.1):
        self.rng = random.Random(seed)
        self.loss, self.dup = loss, dup
        self.queue: list = []
        self.peers: dict = {}

    def sender(self, frm):
        def send(to, payload):
            msg = json.loads(json.dumps(payload))  # what crosses the wire
            if self.rng.random() < self.loss:
                return
            self.queue.append((frm, to, msg))
            if self.rng.random() < self.dup:
                self.queue.append((frm, to, msg))
        return send

    def pump(self, limit=10_000):
        n = 0
        while self.queue and n < limit:
            i = self.rng.randrange(len(self.queue))  # any order
            frm, to, msg = self.queue.pop(i)
            self.peers[to].on_message(frm, msg)
            n += 1


@pytest.mark.parametrize("seed", range(8))
def test_batched_replication_converges_over_a_lossy_link(seed):
    a = _journal(A, A, B, C)
    b = _journal(B, A, B)  # B does not trust C
    c = _journal(C, C)
    _fill(c, 15, "c")
    a.merge(c.events())
    _fill(a, 30, "a")
    _fill(b, 12, "b")
    link = LossyLink(seed)
    pa = ReplicationPeer(a, link.sender(A.did), max_bytes=1500)
    pb = ReplicationPeer(b, link.sender(B.did), max_bytes=1500)
    link.peers = {A.did: pa, B.did: pb}
    for _ in range(200):
        pa.initiate(B.did)
        pb.initiate(A.did)
        link.pump()
        if b.heads().get(A.did) == 30 and a.heads().get(B.did) == 12:
            break
    assert b.heads().get(A.did) == 30 and a.heads().get(B.did) == 12
    assert C.did not in b.heads()  # the untrusted author's events never landed


def test_more_asks_again_only_after_progress():
    a = _journal(A, A)
    b = _journal(B, B)  # B trusts nobody else: A's events are all refused
    _fill(a, 10, "a")
    sent = []
    pb = ReplicationPeer(b, lambda to, p: sent.append(p))
    batch = respond_to_have(a, {"kind": "journal_have", "heads": {}}, max_bytes=500)
    assert batch.get("more")
    assert pb.on_message(A.did, batch) == 0
    assert sent == []  # nothing merged, so no follow-up: a refused batch cannot loop
    ok = _journal(B, A, B)
    pok = ReplicationPeer(ok, lambda to, p: sent.append(p))
    assert pok.on_message(A.did, batch) > 0
    assert sent and sent[-1]["kind"] == "journal_have" and sent[-1]["heads"][A.did] > 0


def test_a_peer_answers_in_capped_batches_and_stops_when_done():
    a = _journal(A, A)
    _fill(a, 10, "a")
    sent = []
    pa = ReplicationPeer(a, lambda to, p: sent.append(p), max_bytes=800)
    pa.on_message(B.did, {"kind": "journal_have", "heads": {}, "reply": True})
    (batch,) = sent
    assert batch.get("more") and len(json.dumps(batch["events"])) <= 1000
    b = _journal(B, A, B)
    follow = []
    pb = ReplicationPeer(b, lambda to, p: follow.append(p))
    last = respond_to_have(a, {"kind": "journal_have", "heads": {A.did: 9}}, max_bytes=800)
    b.merge(a.events()[:9])
    assert "more" not in last and pb.on_message(A.did, last) == 1
    assert follow == []  # the final batch asks for nothing more
