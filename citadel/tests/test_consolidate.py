"""Consolidated memory (S3) and the Socratic memory gate: cautions promoted on
re-derived evidence and retracted on contrary evidence; facts only on a signed,
content-bound operator confirmation; the projection honours revocation; and the
loop closes -- a repeatedly failing action becomes a Gate 1 known-failure."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.action_gate import KNOWN_FAILURE, REJECT, GateContext, PlannedAction, gate  # noqa: E402
from secdogie_citadel.authz import action_hash  # noqa: E402
from secdogie_citadel.consolidate import (  # noqa: E402
    CONFIRMATION_TYPE,
    assert_memory,
    build_memory,
    confirm_and_promote,
    consolidate,
    create_confirmation,
    retract_memory,
    review_candidate,
    verify_confirmation,
)
from secdogie_citadel.episodes import episodes_from_events  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.lessons import CandidateStore, MemoryClass, Tally, make_candidate  # noqa: E402
from secdogie_citadel.run import RunRecorder  # noqa: E402
from secdogie_identity import (  # noqa: E402
    Allowlist,
    Identity,
    MasterSet,
    TrustPolicy,
    cosign,
    create_revocation,
    sign_payload,
)

C, F, P = MemoryClass.CAUTION, MemoryClass.FACT, MemoryClass.PREFERENCE
NODE = Identity.generate()
APP = Identity.generate()  # the operator's Dialogue App session key
CONFIRMERS = Allowlist({APP.did})

CLICK = PlannedAction(kind="click", target_id="element:Save", target_name="Save", expected_observation="saved")
KEY = action_hash(CLICK)


def _counter():
    n = {"t": 0.0}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _journal(ident=NODE, allow=None):
    return Journal(identity=ident, allowlist=allow, clock=_counter())


def _runs(journal, outcomes, key=KEY):
    rec = RunRecorder(journal)
    for outcome in outcomes:
        rid = rec.start_run("g1")
        rec.record_step(rid, observation={"o": 1}, action={"a": key}, result="r", action_key=key, outcome=outcome)
        rec.finish_run(rid, 0)


def _eps(journal):
    return episodes_from_events(journal.events())


def _fact(key="save_button", value="the toolbar Save, not the dialog one", mclass=F, scope="app:cad"):
    return make_candidate(mclass, scope, key, value, source="model", now=1.0)


# ---- review ---------------------------------------------------------------------------


def test_a_caution_needs_enough_failures_and_no_success():
    c = make_candidate(C, "global", KEY, "x", source="consolidation", now=1)
    assert not review_candidate(c, None).promote
    assert not review_candidate(c, Tally(frozenset({"r1", "r2"}), frozenset())).promote
    d = review_candidate(c, Tally(frozenset({"r1", "r2", "r3"}), frozenset()))
    assert d.promote and d.basis == "evidence" and d.evidence == ("r1", "r2", "r3")
    flaky = review_candidate(c, Tally(frozenset({"r1", "r2", "r3"}), frozenset({"r4"})))
    assert not flaky.promote and "flaky" in flaky.reason
    assert not review_candidate(c, None, min_runs=0).promote  # never on zero failures


def test_review_ignores_the_candidates_own_evidence():
    planted = make_candidate(C, "global", KEY, "x", source="consolidation",
                             evidence=[f"fake{i}" for i in range(50)], now=1)
    assert not review_candidate(planted, None).promote


def test_facts_and_preferences_only_ever_go_to_the_operator():
    for c in (_fact(), _fact(mclass=P, key="export_format", value="PDF")):
        d = review_candidate(c, Tally(frozenset({"r1", "r2", "r3"}), frozenset()))
        assert not d.promote and d.needs_operator


# ---- confirmation ---------------------------------------------------------------------


def test_confirmation_is_bound_to_one_memory_on_one_node_and_a_trusted_key():
    c = _fact()
    conf = create_confirmation(APP, c.candidate_id, NODE.did)
    assert verify_confirmation(conf, memory_id=c.candidate_id, subject=NODE.did, confirmers=CONFIRMERS).ok
    bad = [
        dict(memory_id="other", subject=NODE.did, confirmers=CONFIRMERS),
        dict(memory_id=c.candidate_id, subject=Identity.generate().did, confirmers=CONFIRMERS),
        dict(memory_id=c.candidate_id, subject=NODE.did, confirmers=Allowlist({Identity.generate().did})),
        dict(memory_id=c.candidate_id, subject=NODE.did, confirmers=None),
    ]
    for kw in bad:
        assert not verify_confirmation(conf, **kw).ok
    foreign = sign_payload(APP, {"type": "secdogie/something-else/v1", "memory_id": c.candidate_id,
                                 "subject": NODE.did})
    res = verify_confirmation(foreign, memory_id=c.candidate_id, subject=NODE.did, confirmers=CONFIRMERS)
    assert not res.ok and "domain separation" in res.reason
    assert conf["type"] == CONFIRMATION_TYPE


# ---- assert / retract / project -------------------------------------------------------


def test_facts_need_a_verified_confirmation_to_be_asserted():
    j = _journal()
    c = _fact()
    with pytest.raises(ValueError, match="nothing to promote"):
        assert_memory(j, c)
    with pytest.raises(ValueError, match="confirmation refused"):
        assert_memory(j, c, confirmation=create_confirmation(Identity.generate(), c.candidate_id, NODE.did),
                      confirmers=CONFIRMERS)
    evidence = review_candidate(make_candidate(C, "global", KEY, "x", source="consolidation", now=1),
                                Tally(frozenset({"a", "b", "c"}), frozenset()))
    with pytest.raises(ValueError, match="only a caution"):
        assert_memory(j, c, evidence)
    assert_memory(j, c, confirmation=create_confirmation(APP, c.candidate_id, NODE.did), confirmers=CONFIRMERS)
    view = build_memory(j.events(), confirmers=CONFIRMERS)
    rec = view.facts()[("app:cad", "save_button")]
    assert rec.basis == "operator" and rec.confirmed_by == APP.did and rec.author == NODE.did


def test_without_confirmers_no_operator_memory_counts():
    j = _journal()
    c = _fact()
    assert_memory(j, c, confirmation=create_confirmation(APP, c.candidate_id, NODE.did), confirmers=CONFIRMERS)
    assert build_memory(j.events()).facts() == {}


def _forge(journal, **body):
    base = {"op": "assert", "mclass": "fact", "scope": "app:cad", "key": "k", "value": "v",
            "basis": "operator", "evidence": [], "confirmation": {}}
    base.update(body)
    return journal.append("memory", base)


def test_the_projection_rejects_forged_or_mismatched_memories():
    j = _journal()
    real = _fact()
    conf = create_confirmation(APP, real.candidate_id, NODE.did)
    # a genuine confirmation moved under different content
    _forge(j, key=real.key, value="the dialog Save", memory_id=real.candidate_id, confirmation=conf)
    # different content with its own (correct) id, but the confirmation is for another memory
    other = _fact(value="the dialog Save")
    _forge(j, key=other.key, value=other.value, memory_id=other.candidate_id, confirmation=conf)
    # a fact claiming evidence instead of an operator
    _forge(j, key=other.key, value=other.value, memory_id=other.candidate_id, basis="evidence", evidence=["r1"])
    # a caution with no evidence, and one with a bogus basis
    cz = make_candidate(C, "global", KEY, "x", source="consolidation", now=1)
    _forge(j, mclass="caution", scope="global", key=KEY, value="x", memory_id=cz.candidate_id, basis="evidence")
    _forge(j, mclass="caution", scope="global", key=KEY, value="x", memory_id=cz.candidate_id, basis="vibes",
           evidence=["r1"])
    # a secret, and garbage
    j.append("memory", {"op": "assert", "mclass": "fact", "scope": "global", "key": "password", "value": "hunter2",
                        "basis": "operator", "memory_id": "x", "evidence": [], "confirmation": {}})
    j.append("memory", "not a dict")
    j.append("memory", {"op": "assert"})
    assert build_memory(j.events(), confirmers=CONFIRMERS).records == {}


def test_retract_and_supersede():
    j = _journal()
    first = _fact(mclass=P, key="export_format", value="PDF")
    second = _fact(mclass=P, key="export_format", value="PNG")
    for c in (first, second):
        assert_memory(j, c, confirmation=create_confirmation(APP, c.candidate_id, NODE.did), confirmers=CONFIRMERS)
    facts = build_memory(j.events(), confirmers=CONFIRMERS).facts()
    assert [r.value for r in facts.values()] == ["PNG"]  # the newer preference replaced the older
    retract_memory(j, second.candidate_id, reason="operator: forget it")
    assert build_memory(j.events(), confirmers=CONFIRMERS).records == {}


def test_scope_filter_keeps_global_plus_the_scope():
    j = _journal()
    for c in (_fact(scope="app:cad", key="a"), _fact(scope="app:mail", key="b"), _fact(scope="global", key="c")):
        assert_memory(j, c, confirmation=create_confirmation(APP, c.candidate_id, NODE.did), confirmers=CONFIRMERS)
    view = build_memory(j.events(), confirmers=CONFIRMERS, scope="app:cad")
    assert sorted(k for _, k in view.facts()) == ["a", "c"]


def test_a_revoked_authors_memory_drops_out_retroactively():
    master = Identity.generate()
    policy = TrustPolicy(Allowlist({NODE.did}), masters=MasterSet([master.did]))
    j = _journal(allow=policy)
    _runs(j, ["failed"] * 3)
    consolidate(j, CandidateStore(), _eps(j))
    assert KEY in build_memory(j.events(), trust=policy).known_failures
    policy.apply(cosign(master, create_revocation([NODE.did])))
    assert build_memory(j.events(), trust=policy).known_failures == frozenset()


# ---- the loop: consolidate -> gate -----------------------------------------------------


def test_repeated_failure_becomes_a_known_failure_at_the_gate_and_success_retracts_it():
    j = _journal()
    store = CandidateStore()
    _runs(j, ["failed", "no_change"])
    r = consolidate(j, store, _eps(j))
    assert r.asserted == () and len(r.held) == 1  # two runs: not yet
    _runs(j, ["failed"])
    r = consolidate(j, store, _eps(j))
    assert len(r.asserted) == 1 and store.items(mclass=C) == []
    view = build_memory(j.events())
    assert view.known_failures == {KEY}
    assert "avoid repeating" in view.render()
    d = gate(CLICK, GateContext(requires_verification=False, known_failures=view.known_failures))
    assert d.verdict == REJECT and KNOWN_FAILURE in d.findings
    # consolidating again changes nothing
    assert consolidate(j, store, _eps(j)).asserted == ()
    # one success is not enough to lift it...
    _runs(j, ["ok"])
    assert consolidate(j, store, _eps(j)).retracted == ()
    assert build_memory(j.events()).known_failures == {KEY}
    # ...the cause is fixed: three successful runs retract it
    _runs(j, ["ok"] * 2)
    r = consolidate(j, store, _eps(j))
    assert len(r.retracted) == 1
    assert build_memory(j.events()).known_failures == frozenset()
    # and the now-flaky history does not re-promote it
    assert consolidate(j, store, _eps(j)).asserted == ()


def test_a_caution_the_operator_gave_is_not_lifted_by_evidence():
    j = _journal()
    c = make_candidate(C, "global", KEY, "never click the toolbar Save in this build", source="operator", now=1)
    assert_memory(j, c, confirmation=create_confirmation(APP, c.candidate_id, NODE.did), confirmers=CONFIRMERS)
    _runs(j, ["ok"] * 5)
    assert consolidate(j, CandidateStore(), _eps(j), confirmers=CONFIRMERS).retracted == ()
    assert build_memory(j.events(), confirmers=CONFIRMERS).known_failures == {KEY}


def test_operator_confirmed_facts_reach_the_view_and_leave_quarantine():
    j = _journal()
    store = CandidateStore()
    c = store.note("the Save that matters is in the toolbar", key="save_button", scope="app:cad")
    r = consolidate(j, store, _eps(j))
    assert r.awaiting_operator == (c.candidate_id,)
    assert build_memory(j.events(), confirmers=CONFIRMERS).facts() == {}  # quarantined: not in S3
    confirm_and_promote(j, store, c.candidate_id, create_confirmation(APP, c.candidate_id, NODE.did),
                        confirmers=CONFIRMERS)
    view = build_memory(j.events(), confirmers=CONFIRMERS)
    assert view.facts()[("app:cad", "save_button")].value == c.value
    assert "save_button: the Save that matters" in view.render()
    assert store.get(c.candidate_id) is None
    with pytest.raises(KeyError):
        confirm_and_promote(j, store, "gone", {}, confirmers=CONFIRMERS)


def test_memory_replicates_and_is_reverified_on_another_node():
    b_id = Identity.generate()
    allow = Allowlist({NODE.did, b_id.did})
    a, b = _journal(NODE, allow), _journal(b_id, allow)
    _runs(a, ["failed"] * 3)
    consolidate(a, CandidateStore(), _eps(a))
    c = _fact()
    assert_memory(a, c, confirmation=create_confirmation(APP, c.candidate_id, NODE.did), confirmers=CONFIRMERS)
    b.merge(a.events())
    view = build_memory(b.events(), trust=allow, confirmers=CONFIRMERS)
    assert view.known_failures == {KEY} and ("app:cad", "save_button") in view.facts()
    # node B does not trust that App key: the fact does not count there
    assert build_memory(b.events(), trust=allow, confirmers=Allowlist({b_id.did})).facts() == {}
