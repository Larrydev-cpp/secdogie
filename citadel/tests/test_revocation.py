"""Master revocation in Citadel (R2): a revoked author's events stop merging, and
a node whose own DID is revoked stops working -- it does not start, and a
revocation that arrives mid-run halts the supervisor with exit 0."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("nacl")

import secdogie_citadel.cli as cli_mod  # noqa: E402
import secdogie_citadel.supervisor as sup_mod  # noqa: E402
from secdogie_citadel.cli import main  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.supervisor import Supervisor  # noqa: E402
from secdogie_identity import (  # noqa: E402
    ALLOW_ANY,
    Allowlist,
    Identity,
    MasterSet,
    RevocationStore,
    TrustPolicy,
    cosign,
    create_revocation,
)


def _counter():
    n = {"t": 0.0}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


def _revocation(master, dids):
    return cosign(master, create_revocation(dids))


def test_a_revoked_authors_events_stop_merging():
    master = Identity.generate()
    author, reader = Identity.generate(), Identity.generate()
    policy = TrustPolicy(Allowlist({author.did, reader.did}), masters=MasterSet([master.did]))

    theirs = Journal(allowlist=ALLOW_ANY, identity=author, clock=_counter())
    theirs.append("note", {"n": 1})
    ours = Journal(identity=reader, allowlist=policy, clock=_counter())
    assert ours.merge(theirs.events()) == 1           # authorized: merged

    theirs.append("note", {"n": 2})
    policy.apply(_revocation(master, [author.did]))
    assert ours.merge(theirs.events()) == 0           # revoked: the new event is refused


def test_halt_stops_the_running_goal_and_schedules_nothing_more():
    ran = []
    j = Journal(allowlist=ALLOW_ANY, identity=Identity.generate(), clock=_counter())

    def run_task(task, *, should_stop, on_status, confirm, record_step=None, **_):
        ran.append(task)
        sup.halt("test")
        return (5, "stopped") if should_stop() else (0, "ok")

    sup = Supervisor(j, run_task)
    sup.add_goal("g1", title="g1")
    sup.add_goal("g2", title="g2")
    results = sup.run_ready(max_goals=5)
    assert ran == ["g1"] and sup.halted
    assert results == [("g1", 5, "stopped")]


# --- the run command --------------------------------------------------------


def _node_files(tmp_path, master, revoked=()):
    node = Identity.generate()
    key = tmp_path / "node.key"
    node.save(key)
    masters = tmp_path / "masters.conf"
    masters.write_text(f"master_did = {master.did}\n", encoding="utf-8")
    store = tmp_path / "revocations.jsonl"
    (tmp_path / "nodes.allow").write_text(f"authorized_did = {node.did}\n", encoding="utf-8")
    if revoked == "self":
        RevocationStore(store).append(_revocation(master, [node.did]))
    return node, key, masters, store


def test_run_does_not_start_when_this_node_is_revoked(tmp_path, monkeypatch, capsys):
    master = Identity.generate()
    node, key, masters, store = _node_files(tmp_path, master, revoked="self")
    db = str(tmp_path / "j.db")
    main(["add-goal", db, "g1", "--identity", str(key), "--authorized", str(tmp_path / "nodes.allow"), "--title", "g1"])
    calls = []
    monkeypatch.setattr(sup_mod, "agent_run_task", lambda *a, **k: calls.append(1) or (0, "ok"))

    assert main(["run", db, "--identity", str(key), "--authorized", str(tmp_path / "nodes.allow"), "--masters", str(masters),
                 "--revocations", str(store)]) == 0
    assert calls == []
    assert "revoked" in capsys.readouterr().out


def test_run_halts_when_this_node_is_revoked_mid_run(tmp_path, monkeypatch, capsys):
    master = Identity.generate()
    node, key, masters, store = _node_files(tmp_path, master)
    db = str(tmp_path / "j.db")
    main(["add-goal", db, "g1", "--identity", str(key), "--authorized", str(tmp_path / "nodes.allow"), "--title", "g1"])
    main(["add-goal", db, "g2", "--identity", str(key), "--authorized", str(tmp_path / "nodes.allow"), "--title", "g2"])
    monkeypatch.setattr(cli_mod, "_REFRESH_INTERVAL", 0.02)
    ran = []

    def fake_run_task(task, *, should_stop, on_status, confirm, record_step=None, **_):
        ran.append(task)
        # the masters' record reaches this node's store mid-task
        RevocationStore(store).append(_revocation(master, [node.did]))
        deadline = time.time() + 5.0
        while time.time() < deadline and not should_stop():
            time.sleep(0.01)
        return (5, "stopped") if should_stop() else (0, "ran to the end")

    monkeypatch.setattr(sup_mod, "agent_run_task", fake_run_task)
    assert main(["run", db, "--identity", str(key), "--authorized", str(tmp_path / "nodes.allow"), "--masters", str(masters),
                 "--revocations", str(store), "--max-goals", "5"]) == 0
    out = capsys.readouterr().out
    assert ran == ["g1"]                              # g2 never scheduled
    assert "g1: exit 5 -- stopped" in out and "revoked" in out


def test_revocations_need_masters(tmp_path, capsys):
    db = str(tmp_path / "j.db")
    key = tmp_path / "node.key"
    Identity.generate().save(key)
    assert main(["run", db, "--identity", str(key), "--revocations", str(tmp_path / "r.jsonl")]) == 2
    assert "--masters" in capsys.readouterr().err
