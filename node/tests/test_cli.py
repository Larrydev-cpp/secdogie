"""``secdogie-node`` as a process: it refuses to start without its trust sets,
prints one ready line, and exits 0 on SIGTERM; ``status`` reads the journal."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_identity import Allowlist, Identity, MasterSet, cosign, create_revocation  # noqa: E402
from secdogie_node.cli import main  # noqa: E402


def _allow(path, *dids):
    path.write_text("".join(f"authorized_did = {d}\n" for d in dids), encoding="utf-8")
    return str(path)


@pytest.fixture
def files(tmp_path):
    node, app, op = Identity.generate(), Identity.generate(), Identity.generate()
    node.save(tmp_path / "node.key")
    return {
        "node": node,
        "args": ["run", "--identity", str(tmp_path / "node.key"),
                 "--apps", _allow(tmp_path / "apps.allow", app.did),
                 "--operators", _allow(tmp_path / "operators.allow", op.did),
                 "--authorized", _allow(tmp_path / "nodes.allow", node.did),
                 "--journal", str(tmp_path / "node.db"), "--listen", "127.0.0.1:0"],
        "tmp": tmp_path,
    }


@pytest.mark.parametrize("drop", ["--apps", "--operators", "--authorized"])
def test_run_refuses_to_start_without_a_trust_set(files, drop, capsys):
    args = list(files["args"])
    i = args.index(drop)
    del args[i:i + 2]
    with pytest.raises(SystemExit) as e:
        main(args)
    assert e.value.code == 2 and drop in capsys.readouterr().err


def test_the_process_announces_itself_and_stops_cleanly_on_sigterm(files):
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "-m", "secdogie_node.cli", *files["args"]],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        line = proc.stdout.readline()
        ready = json.loads(line)
        assert ready["event"] == "ready" and ready["did"] == files["node"].did
        assert ready["listen"].startswith("127.0.0.1:")
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    err = proc.stderr.read()
    assert "every mutating action is refused" in err  # no --issuers: said out loud
    assert "stopped" in err


def test_run_checks_its_flags_before_starting(files, capsys):
    with pytest.raises(SystemExit):
        main([*files["args"], "--revocations", "r.jsonl"])
    assert "--masters" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main([*files["args"], "--app-binding", "app.binding.json"])
    assert "--transport-key" in capsys.readouterr().err


def test_a_revoked_node_does_not_start(files, capsys, monkeypatch):
    import secdogie_node.node as node_mod

    def refuse(*a, **k):
        raise AssertionError("a revoked node was started")

    monkeypatch.setattr(node_mod, "Node", refuse)
    master = Identity.generate()
    masters = files["tmp"] / "masters.conf"
    masters.write_text(f"master_did = {master.did}\n", encoding="utf-8")
    record = cosign(master, create_revocation([files["node"].did], reason="test"))
    store = files["tmp"] / "revocations.jsonl"
    store.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert MasterSet.load(str(masters))
    assert main([*files["args"], "--masters", str(masters), "--revocations", str(store)]) == 0
    assert capsys.readouterr().out == ""  # no ready line: it never started


def test_status_reads_the_journal(files, capsys):
    node = files["node"]
    j = Journal(str(files["tmp"] / "node.db"), identity=node, allowlist=Allowlist({node.did}))
    j.append("goal", {"op": "add", "id": "g1", "title": "file the report", "deps": []})
    assert main(["status", "--journal", str(files["tmp"] / "node.db"),
                 "--authorized", str(files["tmp"] / "nodes.allow")]) == 0
    out = capsys.readouterr().out
    assert "g1" in out and "file the report" in out and "memory: 0" in out


def test_two_processes_app_and_node_over_udp(files):
    """The operator App and the node as separate processes: a goal submitted by
    the App reaches the node's Supervisor, and the node reports how it ended.
    (No model key here, so the goal ends with the runner's clear refusal --
    the wiring between the processes is what is under test.)"""
    pytest.importorskip("secdogie_transport")
    tmp = files["tmp"]
    app = Identity.generate()
    app.save(tmp / "app.key")
    _allow(tmp / "apps.allow", app.did)  # this App is the one the node trusts
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.update(PYTHONUNBUFFERED="1", HOME=str(tmp), XDG_CONFIG_HOME=str(tmp), APPDATA=str(tmp))
    node = subprocess.Popen([sys.executable, "-m", "secdogie_node.cli", *files["args"]],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        ready = json.loads(node.stdout.readline())
        script = tmp / "script.jsonl"
        script.write_text("\n".join(json.dumps(s) for s in [
            {"op": "add_goal", "title": "file the report", "goal_id": "g1"},
            {"op": "expect_status", "match": "goal g1 finished"},
        ]) + "\n", encoding="utf-8")
        app_run = subprocess.run(
            [sys.executable, "-m", "secdogie_dialogue.cli", "connect", "--identity", str(tmp / "app.key"),
             "--node", ready["did"], "--node-addr", ready["listen"], "--listen", "127.0.0.1:0",
             "--headless", str(script), "--step-timeout", "20"],
            capture_output=True, text=True, env=env, timeout=60)
        results = [json.loads(line) for line in app_run.stdout.splitlines()]
        assert app_run.returncode == 0, (results, app_run.stderr[-2000:])
        assert results[0]["reply"].startswith("accepted: goal g1 queued")
        assert "goal g1 finished: exit 1" in results[1]["status"]
        node.send_signal(signal.SIGTERM)
        assert node.wait(timeout=20) == 0
    finally:
        if node.poll() is None:
            node.kill()
            node.wait()
