from __future__ import annotations

import pytest
from secdogie_citadel.state import StateDelta, StateStore, delta_from_event


def _d(author, seq, lamport, etype, eid, op, payload=None):
    return StateDelta(author, seq, lamport, etype, eid, op, payload or {})


def test_set_patch_delete_fold():
    store = StateStore()
    store.merge([
        _d("a", 1, 1, "goal", "g1", "set", {"title": "root", "status": "pending"}),
        _d("a", 2, 2, "goal", "g1", "patch", {"status": "done"}),
        _d("a", 3, 3, "goal", "g2", "set", {"title": "x"}),
        _d("a", 4, 4, "goal", "g2", "delete"),
    ])
    state = store.materialize()
    assert state["goal"]["g1"] == {"title": "root", "status": "done"}
    assert "g2" not in state["goal"]


def test_lww_by_total_order_across_authors():
    store = StateStore()
    # b's patch has a higher lamport, so it wins regardless of merge order
    store.merge([
        _d("b", 1, 5, "task", "t1", "patch", {"owner": "b"}),
        _d("a", 1, 3, "task", "t1", "set", {"owner": "a", "n": 1}),
    ])
    assert store.get("task", "t1") == {"owner": "b", "n": 1}


def test_apply_is_idempotent():
    store = StateStore()
    d = _d("a", 1, 1, "run", "r1", "set", {"state": "created"})
    assert store.apply(d) is True
    assert store.apply(d) is False  # same (author, seq) -> no-op
    assert store.merge([d]) == 0
    assert store.get("run", "r1") == {"state": "created"}


def test_delete_then_set_resurrects():
    store = StateStore()
    store.merge([
        _d("a", 1, 1, "goal", "g", "set", {"v": 1}),
        _d("a", 2, 2, "goal", "g", "delete"),
        _d("a", 3, 3, "goal", "g", "set", {"v": 2}),
    ])
    assert store.get("goal", "g") == {"v": 2}


def test_bad_operation_rejected():
    with pytest.raises(ValueError):
        StateDelta("a", 1, 1, "goal", "g", "explode", {})
    with pytest.raises(ValueError):
        StateDelta("a", 1, 1, "", "g", "set", {})


def test_delta_from_event_roundtrip():
    event = {
        "kind": "state", "author": "did:key:zA", "seq": 7, "lamport": 9,
        "body": {"entity_type": "capability", "entity_id": "c1", "operation": "set",
                 "payload": {"scope": "desktop.observe"}},
    }
    d = delta_from_event(event)
    assert d is not None
    assert d.author == "did:key:zA" and d.seq == 7 and d.lamport == 9
    assert d.entity_type == "capability" and d.operation == "set"
    assert delta_from_event({"kind": "note", "body": {}}) is None  # not a state event


def test_end_to_end_over_a_real_journal():
    pytest.importorskip("nacl")
    from secdogie_citadel.journal import Journal
    from secdogie_citadel.state import record_state
    from secdogie_identity import Identity

    j = Journal(identity=Identity.generate())
    record_state(j, "goal", "g1", "set", {"title": "tidy"})
    record_state(j, "goal", "g1", "patch", {"status": "done"})
    record_state(j, "knowledge", "obs-1", "set",
                 {"observation_id": "obs-1", "content_hash": "sha256:abc", "metadata": {"source": "dib"}})

    store = StateStore()
    store.merge_events(j.events())
    assert store.get("goal", "g1") == {"title": "tidy", "status": "done"}
    # large visual data is referenced by hash, not inlined
    assert store.get("knowledge", "obs-1")["content_hash"] == "sha256:abc"
    ok, _ = j.verify()
    assert ok
