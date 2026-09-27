"""Candidate memory (S2): the local quarantine. Validated, secret-free, merged by
id, expiring, capped; cautions distilled only from usable episodes."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.episodes import Episode, StepRecord  # noqa: E402
from secdogie_citadel.lessons import (  # noqa: E402
    CandidateStore,
    MemoryClass,
    SecretRefused,
    candidate_id,
    extract_cautions,
    make_candidate,
    tally,
)

C, F = MemoryClass.CAUTION, MemoryClass.FACT


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _cand(key="k", value="v", mclass=F, scope="global", evidence=(), now=1000.0, source="model"):
    return make_candidate(mclass, scope, key, value, source=source, evidence=evidence, now=now)


# ---- validation -----------------------------------------------------------------


def test_ids_are_content_derived():
    assert candidate_id(F, "global", "k", "v") == candidate_id(F, "global", "k", "v")
    assert candidate_id(F, "global", "k", "v") != candidate_id(F, "global", "k", "w")
    assert candidate_id(F, "global", "k", "v") != candidate_id(C, "global", "k", "v")
    assert _cand().candidate_id == candidate_id(F, "global", "k", "v")


@pytest.mark.parametrize("kw,err", [
    ({"scope": "everywhere"}, "bad scope"),
    ({"scope": "app:"}, "bad scope"),
    ({"key": " "}, "key must be"),
    ({"key": "k" * 201}, "key must be"),
    ({"value": ""}, "value must be"),
    ({"value": "v" * 501}, "value must be"),
    ({"source": "rumor"}, "unknown source"),
    ({"mclass": "gossip"}, "unknown memory class"),
])
def test_bad_candidates_are_refused(kw, err):
    with pytest.raises(ValueError, match=err):
        _cand(**kw)


@pytest.mark.parametrize("key,value", [
    ("github_token", "abc"),
    ("note", "use sk-ABCDEFGHIJKLMNOPQRSTUV to log in"),
    ("k", "-----BEGIN OPENSSH PRIVATE KEY-----"),
])
def test_secrets_never_enter_memory(key, value):
    with pytest.raises(SecretRefused):
        _cand(key=key, value=value)
    with pytest.raises(SecretRefused):
        CandidateStore().note(value, key=key)


def test_the_store_validates_what_it_is_handed_directly():
    from secdogie_citadel.lessons import Candidate

    store = CandidateStore()
    with pytest.raises(SecretRefused):
        store.upsert(Candidate("x", F, "global", "password", "hunter2", "model", (), (), 0.0, 0.0))
    with pytest.raises(ValueError):
        store.upsert(Candidate("y", F, "nowhere", "k", "v", "model", (), (), 0.0, 0.0))
    assert store.items() == []


def test_scopes():
    for scope in ("global", "app:com.example.cad", "goal:g1"):
        assert _cand(scope=scope).scope == scope


# ---- the store ------------------------------------------------------------------


def test_upsert_merges_evidence_by_id():
    store = CandidateStore()
    store.upsert(_cand(mclass=C, evidence=("r1", "r2"), now=1000))
    merged = store.upsert(_cand(mclass=C, evidence=("r2", "r3"), now=2000))
    assert merged.evidence == ("r1", "r2", "r3")
    assert merged.first_seen == 1000 and merged.last_seen == 2000
    assert store.get(merged.candidate_id) == merged
    assert len(store.items()) == 1


def test_items_filter_and_order():
    store = CandidateStore()
    store.upsert(_cand(key="a", mclass=C, now=1))
    store.upsert(_cand(key="b", now=3))
    store.upsert(_cand(key="c", scope="goal:g1", now=2))
    assert [c.key for c in store.items()] == ["b", "c", "a"]
    assert [c.key for c in store.items(mclass=C)] == ["a"]
    assert [c.key for c in store.items(scope="goal:g1")] == ["c"]
    assert store.remove(store.items(mclass=C)[0].candidate_id) and not store.remove("nope")


def test_stale_candidates_expire():
    clock = Clock(10_000)
    store = CandidateStore(clock=clock, ttl=100)
    store.upsert(_cand(key="old", now=9_899))
    store.upsert(_cand(key="fresh", now=9_950))
    assert store.expire() == 1
    assert [c.key for c in store.items()] == ["fresh"]


def test_the_store_is_capped_evicting_the_least_recently_seen():
    store = CandidateStore(max_items=2)
    store.upsert(_cand(key="a", now=1))
    store.upsert(_cand(key="b", now=2))
    store.upsert(_cand(key="c", now=3))
    assert sorted(c.key for c in store.items()) == ["b", "c"]
    store.upsert(_cand(key="b", now=4))  # merging an existing one evicts nothing
    assert sorted(c.key for c in store.items()) == ["b", "c"]


def test_model_notes_are_quarantined_facts():
    store = CandidateStore(clock=Clock(5))
    c = store.note("the Save that matters is in the toolbar", scope="app:cad")
    assert c.mclass is F and c.source == "model" and c.scope == "app:cad" and c.key.startswith("note:")
    assert store.note("export as PDF", key="export_format", mclass=MemoryClass.PREFERENCE).key == "export_format"
    with pytest.raises(ValueError):
        store.note("   ")


def test_persisted_across_reopen(tmp_path):
    path = str(tmp_path / "candidates.db")
    s1 = CandidateStore(path)
    c = s1.upsert(_cand(mclass=C, evidence=("r1",)))
    s1.close()
    assert CandidateStore(path).get(c.candidate_id) == c


# ---- distilling cautions --------------------------------------------------------


def _ep(run_id, steps, *, state="completed", verified=True):
    recs = tuple(StepRecord(run_id, i + 1, key, "allow", (), outcome, "") for i, (key, outcome) in enumerate(steps))
    return Episode(run_id, "g", state, 0, recs, verified)


def test_tally_counts_runs_not_repeats_and_only_usable_episodes():
    eps = [
        _ep("r1", [("k", "failed"), ("k", "failed"), ("k", "no_change")]),
        _ep("r2", [("k", "no_change"), ("j", "ok")]),
        _ep("r3", [("k", "ok")]),
        _ep("r4", [("k", "failed")], state="executing"),  # unfinished
        _ep("r5", [("k", "failed")], verified=False),  # tampered
        _ep("r6", [("k", "rejected"), ("", "failed"), ("k", "unknown")]),  # never ran / no key / unreported
    ]
    t = tally(eps)
    assert t["k"].failure_runs == {"r1", "r2"} and t["k"].success_runs == {"r3"}
    assert t["j"].failure_runs == frozenset() and t["j"].success_runs == {"r2"}
    assert set(t) == {"k", "j"}


def test_extract_cautions_for_failed_actions_only():
    eps = {e.run_id: e for e in [_ep("r1", [("k", "failed")]), _ep("r2", [("k", "ok"), ("j", "ok")])]}
    cands = extract_cautions(eps, now=7)
    assert [(c.mclass, c.key, c.evidence, c.contradictions, c.source) for c in cands] == [
        (C, "k", ("r1",), ("r2",), "consolidation")]


def test_the_secret_net_matches_the_agents():
    agent_memory = pytest.importorskip("secdogie_agent.memory")
    from secdogie_citadel import lessons

    assert lessons._SECRET_KEY_HINTS == agent_memory._SECRET_KEY_HINTS
    assert lessons._SECRET_VALUE_RE.pattern == agent_memory._SECRET_VALUE_RE.pattern
