"""Operator CLI: capability grants reach the journal and turn on enforcement.

These exercise the real argparse entry points (`secdogie_citadel.cli.main`) on a
temporary journal, the same way the other CLIs are tested."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.cli import main  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.state import StateStore  # noqa: E402
from secdogie_identity import ALLOW_ANY, Identity  # noqa: E402
from secdogie_identity.capability import create_capability  # noqa: E402


def _keyfile(tmp_path, name):
    idn = Identity.generate()
    path = tmp_path / name
    idn.save(path)
    return path, idn


def _allow(tmp_path, *dids):
    path = tmp_path / "nodes.allow"
    path.write_text("".join(f"authorized_did = {d}\n" for d in dids), encoding="utf-8")
    return str(path)


def _write_grant(tmp_path, name, grant):
    path = tmp_path / name
    path.write_text(json.dumps(grant), encoding="utf-8")
    return path


def test_add_grant_then_scopes(tmp_path, capsys):
    db = str(tmp_path / "j.db")
    node_key, node = _keyfile(tmp_path, "node.key")
    op = Identity.generate()

    grant = create_capability(op, node.did, ["physical.click"], ttl=3600)
    gpath = _write_grant(tmp_path, "g.json", grant)
    issuers = tmp_path / "issuers.conf"
    issuers.write_text(f"authorized_did = {op.did}\n", encoding="utf-8")

    assert main(["add-grant", db, str(gpath), "--identity", str(node_key), "--authorized", _allow(tmp_path, node.did),
                 "--issuers", str(issuers)]) == 0
    assert "added grant" in capsys.readouterr().out

    assert main(["scopes", db, "--identity", str(node_key), "--authorized", _allow(tmp_path, node.did), "--issuers", str(issuers)]) == 0
    assert "physical.click" in capsys.readouterr().out


def test_add_grant_refuses_untrusted(tmp_path, capsys):
    db = str(tmp_path / "j.db")
    node_key, node = _keyfile(tmp_path, "node.key")
    op = Identity.generate()
    rogue = Identity.generate()

    # signed by a rogue issuer, but the allowlist only trusts `op`
    grant = create_capability(rogue, node.did, ["physical.click"], ttl=3600)
    gpath = _write_grant(tmp_path, "g.json", grant)
    issuers = tmp_path / "issuers.conf"
    issuers.write_text(f"authorized_did = {op.did}\n", encoding="utf-8")

    assert main(["add-grant", db, str(gpath), "--identity", str(node_key), "--authorized", _allow(tmp_path, node.did),
                 "--issuers", str(issuers)]) == 1
    assert "refusing to add grant" in capsys.readouterr().out

    # nothing was written to the journal
    events = [e for e in Journal(db, allowlist=ALLOW_ANY).events() if e["kind"] == "capability_grant"]
    assert events == []


def test_run_issuers_gates_the_task(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "j.db")
    node_key, node = _keyfile(tmp_path, "node.key")
    op = Identity.generate()

    # a granted click, no type
    grant = create_capability(op, node.did, ["physical.click"], ttl=3600)
    gpath = _write_grant(tmp_path, "g.json", grant)
    issuers = tmp_path / "issuers.conf"
    issuers.write_text(f"authorized_did = {op.did}\n", encoding="utf-8")
    main(["add-grant", db, str(gpath), "--identity", str(node_key), "--authorized", _allow(tmp_path, node.did), "--issuers", str(issuers)])
    capsys.readouterr()

    seen = {}

    def fake_run_task(task, *, should_stop, on_status, confirm, record_step=None,
                      plan_gate=None, recovery=None):
        seen["click"] = plan_gate({"kind": "left_click", "x": 1, "y": 1}, [])
        seen["type"] = plan_gate({"kind": "type", "text": "hi"}, [])
        return (0, "ok")

    import secdogie_citadel.supervisor as sup_mod
    monkeypatch.setattr(sup_mod, "agent_run_task", fake_run_task)

    # a ready goal to run
    main(["add-goal", db, "g1", "--identity", str(node_key), "--authorized", _allow(tmp_path, node.did), "--title", "g1"])
    capsys.readouterr()

    assert main(["run", db, "--identity", str(node_key), "--authorized", _allow(tmp_path, node.did), "--issuers", str(issuers)]) == 0
    out = capsys.readouterr().out
    assert "capability enforcement on: physical.click" in out
    assert seen["click"][0] is True
    assert seen["type"][0] is False

    # the run was recorded in the journal state
    store = StateStore()
    store.merge_events(Journal(db, allowlist=ALLOW_ANY).events())
    assert store.entities("run")


def test_every_command_needs_to_say_whose_events_it_trusts(tmp_path, capsys):
    db = str(tmp_path / "j.db")
    node_key, node = _keyfile(tmp_path, "node.key")
    for argv in (["verify", db], ["goals", db], ["log", db], ["add-goal", db, "g1", "--identity", str(node_key)],
                 ["run", db, "--identity", str(node_key)]):
        assert main(argv) == 2, argv
        assert "--authorized" in capsys.readouterr().err
    assert main(["add-goal", db, "g1", "--identity", str(node_key), "--insecure-dev"]) == 0
    assert "WARNING: --insecure-dev" in capsys.readouterr().err
    assert main(["verify", db, "--authorized", _allow(tmp_path, node.did)]) == 0


def test_run_without_issuers_refuses_every_mutating_action(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "j.db")
    node_key, node = _keyfile(tmp_path, "node.key")
    allow = _allow(tmp_path, node.did)
    seen = {}

    def fake_run_task(task, *, should_stop, on_status, confirm, record_step=None, plan_gate=None, **_):
        seen["gate"] = plan_gate
        seen["click"] = plan_gate({"kind": "left_click", "x": 1, "y": 1}, []) if plan_gate else None
        return (0, "ok")

    import secdogie_citadel.supervisor as sup_mod
    monkeypatch.setattr(sup_mod, "agent_run_task", fake_run_task)
    main(["add-goal", db, "g1", "--identity", str(node_key), "--authorized", allow])
    assert main(["run", db, "--identity", str(node_key), "--authorized", allow]) == 0
    assert "every mutating action will be refused" in capsys.readouterr().out
    assert seen["click"][0] is False

    main(["add-goal", db, "g2", "--identity", str(node_key), "--authorized", allow])
    assert main(["run", db, "--identity", str(node_key), "--authorized", allow, "--insecure-dev"]) == 0
    assert "NO capability check" in capsys.readouterr().err
    assert seen["gate"] is None  # said out loud: no gate at all

