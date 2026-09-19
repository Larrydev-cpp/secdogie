from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.journal import GENESIS, Journal  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402


def _counter():
    n = {"t": 0.0}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def test_append_chains_and_signs():
    idn = Identity.generate()
    j = Journal(identity=idn, clock=_counter())
    e1 = j.append("note", {"x": 1})
    e2 = j.append("note", {"x": 2})
    assert e1["author"] == idn.did
    assert e1["seq"] == 1 and e1["prev_hash"] == GENESIS
    assert e2["seq"] == 2 and e2["prev_hash"] == e1["entry_hash"]
    ok, reason = j.verify()
    assert ok, reason


def test_append_requires_identity():
    j = Journal()  # replica, no key
    with pytest.raises(RuntimeError):
        j.append("note", {})


def test_merge_is_idempotent_and_converges():
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter())

    ea = [ja.append("note", {"n": i}) for i in range(3)]
    eb = [jb.append("note", {"n": i}) for i in range(2)]

    # cross-merge
    assert jb.merge(ea) == 3
    assert ja.merge(eb) == 2
    # re-merging the same events changes nothing (idempotent)
    assert jb.merge(ea) == 0
    assert ja.merge(eb) == 0

    # both nodes now agree on the full set and its total order
    assert [(e["author"], e["seq"]) for e in ja.events()] == [(e["author"], e["seq"]) for e in jb.events()]
    ok, _ = ja.verify()
    assert ok


def test_merge_rejects_unauthorized_did():
    a, stranger = Identity.generate(), Identity.generate()
    j = Journal(allowlist=Allowlist({a.did}))
    js = Journal(identity=stranger, clock=_counter())
    ev = js.append("note", {})
    assert j.merge([ev]) == 0  # stranger not on the allowlist
    assert j.events() == []


def test_merge_rejects_tampered_event():
    a = Identity.generate()
    j = Journal(allowlist=Allowlist({a.did}))
    src = Journal(identity=a, clock=_counter())
    ev = src.append("note", {"amount": 1})
    ev["body"] = {"amount": 999}  # tamper after signing
    assert j.merge([ev]) == 0


def test_merge_handles_out_of_order_within_author():
    a = Identity.generate()
    src = Journal(identity=a, clock=_counter())
    e1 = src.append("note", {"n": 1})
    e2 = src.append("note", {"n": 2})
    e3 = src.append("note", {"n": 3})
    j = Journal(allowlist=Allowlist({a.did}))
    # deliberately shuffled; merge() sorts by (author, seq) so the chain applies in order
    assert j.merge([e3, e1, e2]) == 3
    ok, _ = j.verify()
    assert ok


def test_merge_drops_gap_until_predecessor_arrives():
    a = Identity.generate()
    src = Journal(identity=a, clock=_counter())
    e1 = src.append("note", {"n": 1})
    e2 = src.append("note", {"n": 2})
    j = Journal(allowlist=Allowlist({a.did}))
    assert j.merge([e2]) == 0        # seq 2 with no seq 1 -> held back
    assert j.merge([e1, e2]) == 2    # predecessor arrives -> both apply


def test_heads_and_since():
    a = Identity.generate()
    j = Journal(identity=a, clock=_counter())
    for i in range(3):
        j.append("note", {"n": i})
    assert j.heads() == {a.did: 3}
    tail = j.since(a.did, 1)
    assert [e["seq"] for e in tail] == [2, 3]


def test_total_order_is_lamport_then_tiebreak():
    a, b = Identity.generate(), Identity.generate()
    allow = Allowlist({a.did, b.did})
    ja = Journal(identity=a, allowlist=allow, clock=_counter())
    jb = Journal(identity=b, allowlist=allow, clock=_counter())
    e_a1 = ja.append("note", {})
    jb.merge([e_a1])           # b sees a1 -> b's lamport advances past it
    e_b1 = jb.append("note", {})
    ja.merge([e_b1])
    order = [(e["author"], e["seq"]) for e in ja.events()]
    assert order == [(a.did, 1), (b.did, 1)]  # a1 causally precedes b1
