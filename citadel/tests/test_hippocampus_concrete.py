"""Unit tests for concrete coordinate-indexed memory in Hippocampus."""
from __future__ import annotations

import numpy as np
import pytest

from secdogie_citadel.cortex.curriculum import Lesson
from secdogie_citadel.cortex.hippocampus import ConcreteAnchor, Hippocampus, slot_compatibility


def _unit(vec: list[float]) -> np.ndarray:
    arr = np.array(vec, dtype=np.float32)
    return arr / float(np.linalg.norm(arr))


def test_concrete_anchor_prevents_magnitude_blurring_on_identical_neural_coordinate() -> None:
    """Two transfers sharing nearly identical neural firing coordinates must NOT blur when amounts/recipients diverge."""
    hip = Hippocampus()
    coord = _unit([1.0, 0.2, 0.0, 0.0])

    safe_lesson = Lesson(
        text="给自己名下卡转 500 元",
        answers={"kind": "task", "stakes": "small", "who": "self"},
        source="person",
    )
    safe_anchor = ConcreteAnchor(
        verbatim="给自己名下卡转 500 元",
        tool="bank_transfer",
        entities=("self_savings_account",),
        quantities=(("amount", 500.0, "CNY"),),
        facts=(("counterparty_verified", "yes"),),
        timestamp_ms=1730000000000,
        journal_ref="ed25519:evt_safe_500",
    )
    hip.keep(safe_lesson, coord, anchor=safe_anchor)

    # Query has nearly identical neural coordinate, but 100x amount & unverified stranger!
    scam_query = ConcreteAnchor(
        verbatim="转 50000 元到陌生安全账户",
        tool="bank_transfer",
        entities=("unknown_safe_account",),
        quantities=(("amount", 50000.0, "CNY"),),
        facts=(("counterparty_verified", "no"),),
    )
    recalled_blind = hip.recall(coord[None, :], sharpness=10.0, trust=0.7)[0]
    recalled_verified = hip.recall(
        coord[None, :],
        sharpness=10.0,
        trust=0.7,
        query_anchors=[scam_query],
    )[0]

    # Without concrete slot verification, identical coordinates blindly recall at weight ~1.0
    assert recalled_blind.weight > 0.95
    # With concrete slot verification, 100x amount + disjoint entity + fact flip drops trust to 0!
    assert recalled_verified.weight == pytest.approx(0.0)
    assert recalled_verified.concrete[0].slot_compatibility < 0.05
    assert any(m.startswith("quantity:amount") for m in recalled_verified.concrete[0].mismatches)
    assert "entities:disjoint" in recalled_verified.concrete[0].mismatches
    assert "fact:counterparty_verified(no!=yes)" in recalled_verified.concrete[0].mismatches


def test_concrete_anchor_reranks_exact_match_above_closer_blurry_neighbor() -> None:
    """When two memories live near the same coordinate, exact path/entity match wins over closer blurry neighbor."""
    hip = Hippocampus()
    # Neighbor A is slightly closer in raw neural cosine, but targets /tmp/cache
    coord_a = _unit([0.995, 0.10, 0.0, 0.0])
    # Neighbor B is slightly further in raw neural cosine, but matches exact sensitive path ~/.ssh/id_rsa
    coord_b = _unit([0.96, 0.28, 0.0, 0.0])
    query_coord = _unit([1.0, 0.0, 0.0, 0.0])

    hip.keep(
        Lesson(text="read /tmp/cache", answers={"kind": "task", "stakes": "small"}, source="person"),
        coord_a,
        anchor=ConcreteAnchor(
            verbatim="read /tmp/cache",
            tool="fs_read",
            paths=("/tmp/cache",),
            journal_ref="evt_tmp",
        ),
    )
    hip.keep(
        Lesson(text="read ~/.ssh/id_rsa", answers={"kind": "task", "stakes": "huge"}, source="person"),
        coord_b,
        anchor=ConcreteAnchor(
            verbatim="read ~/.ssh/id_rsa",
            tool="fs_read",
            paths=("~/.ssh/id_rsa",),
            journal_ref="evt_ssh",
        ),
    )

    q_anchor = ConcreteAnchor(tool="fs_read", paths=("~/.ssh/id_rsa",))
    res = hip.recall(query_coord[None, :], sharpness=12.0, trust=0.5, query_anchors=[q_anchor])[0]

    assert res.lessons[0] == 1
    assert res.concrete[0].anchor.journal_ref == "evt_ssh"
    assert res.concrete[0].anchor.paths == ("~/.ssh/id_rsa",)
    assert res.concrete[0].slot_compatibility == pytest.approx(1.0)
    assert res.weight > 0.85


def test_anchors_stay_aligned_across_eviction_and_disputed_decay() -> None:
    hip = Hippocampus(capacity=2)
    c1 = _unit([1.0, 0.0])
    c2 = _unit([0.0, 1.0])
    c3 = _unit([0.7, 0.7])

    hip.keep(
        Lesson(text="disputed", answers={"kind": "chat"}, disputed=True, priority_trace=0.1),
        c1,
        anchor=ConcreteAnchor(verbatim="disputed", journal_ref="evt_1"),
    )
    hip.keep(
        Lesson(text="kept_person", answers={"kind": "chat"}, source="person"),
        c2,
        anchor=ConcreteAnchor(verbatim="kept_person", journal_ref="evt_2"),
    )
    # Adding 3rd item evicts the disputed item (index 0)
    hip.keep(
        Lesson(text="third", answers={"kind": "chat"}, source="person"),
        c3,
        anchor=ConcreteAnchor(verbatim="third", journal_ref="evt_3"),
    )

    assert len(hip.lessons) == 2
    assert len(hip.anchors) == 2
    assert [a.journal_ref for a in hip.anchors] == ["evt_2", "evt_3"]

    # Add a disputed item and decay it out
    hip.capacity = 5
    hip.keep(
        Lesson(text="temp_disputed", answers={"kind": "chat"}, disputed=True, priority_trace=0.15),
        c1,
        anchor=ConcreteAnchor(verbatim="temp_disputed", journal_ref="evt_4"),
    )
    evicted = hip.decay_disputed(decay=0.5, min_trace=0.1)
    assert evicted == 1
    assert [a.journal_ref for a in hip.anchors] == ["evt_2", "evt_3"]


def test_slot_compatibility_handles_legacy_unstructured_anchors() -> None:
    score, mismatches = slot_compatibility(ConcreteAnchor(verbatim="hi"), ConcreteAnchor(verbatim="hello"))
    assert score == 1.0
    assert mismatches == ()
