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
                 "--mesh", _allow(tmp_path / "peers.allow", node.did),
                 "--journal", str(tmp_path / "node.db"), "--listen", "127.0.0.1:0"],
        "tmp": tmp_path,
    }


@pytest.mark.parametrize("drop", ["--apps", "--operators", "--authorized", "--mesh"])
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
        from secdogie_transport.membership import verify_record

        rec = verify_record(ready["record"], allowlist=Allowlist({files["node"].did}))
        assert rec is not None and f"{rec.endpoints.best().host}:{rec.endpoints.best().port}" == ready["listen"]
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


def test_the_app_finds_the_node_by_did_at_a_rendezvous(files):
    """Three processes: ``secdogie-relay --rendezvous``, the node (registered
    there with ``--rendezvous-record``) and the App, which is given the node's
    DID and the rendezvous' record -- never the node's address."""
    pytest.importorskip("secdogie_transport")
    tmp = files["tmp"]
    app, rv = Identity.generate(), Identity.generate()
    app.save(tmp / "app.key")
    rv.save(tmp / "rv.key")
    _allow(tmp / "apps.allow", app.did)
    _allow(tmp / "mesh.allow", files["node"].did, app.did)
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.update(PYTHONUNBUFFERED="1", HOME=str(tmp), XDG_CONFIG_HOME=str(tmp), APPDATA=str(tmp))
    procs = []
    try:
        relay = subprocess.Popen(
            [sys.executable, "-m", "secdogie_transport.relay_node", "--identity", str(tmp / "rv.key"),
             "--authorized", str(tmp / "mesh.allow"), "--listen", "127.0.0.1:0", "--rendezvous"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        procs.append(relay)
        (tmp / "rv.json").write_text(relay.stdout.readline(), encoding="utf-8")
        node = subprocess.Popen(
            [sys.executable, "-m", "secdogie_node.cli", *files["args"], "--rendezvous-record", str(tmp / "rv.json")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        procs.append(node)
        ready = json.loads(node.stdout.readline())
        script = tmp / "script.jsonl"
        script.write_text("\n".join(json.dumps(s) for s in [
            {"op": "add_goal", "title": "file the report", "goal_id": "g1"},
            {"op": "expect_status", "match": "goal g1 finished"},
        ]) + "\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "secdogie_dialogue.cli", "connect", "--identity", str(tmp / "app.key"),
               "--node", ready["did"], "--rendezvous-record", str(tmp / "rv.json"), "--listen", "127.0.0.1:0",
               "--headless", str(script), "--step-timeout", "20"]
        for _ in range(50):  # the node registers right after it starts; give that a moment
            app_run = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
            if "not registered" not in app_run.stderr:
                break
        results = [json.loads(line) for line in app_run.stdout.splitlines()]
        assert app_run.returncode == 0, (results, app_run.stderr[-2000:])
        assert results[0]["reply"].startswith("accepted: goal g1 queued")
        assert "goal g1 finished" in results[1]["status"]
        for p in reversed(procs):
            p.send_signal(signal.SIGTERM)
            assert p.wait(timeout=20) == 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait()


def test_the_app_says_so_when_the_node_is_not_at_the_rendezvous(files, tmp_path, capsys):
    pytest.importorskip("secdogie_transport")
    from secdogie_dialogue.cli import main as app_main
    from secdogie_transport import DirectUDPTransport, Endpoint, RendezvousService, UDPChannel
    from secdogie_transport.membership import ROLE_RENDEZVOUS, sign_record

    app, rv = Identity.generate(), Identity.generate()
    app.save(tmp_path / "app.key")
    channel = UDPChannel("127.0.0.1", 0)
    transport = DirectUDPTransport(rv, channel, allowlist=Allowlist({app.did}))
    RendezvousService(transport, allowlist=Allowlist({app.did, files["node"].did}))
    record = sign_record(rv, [Endpoint("local", *channel.address)], last_seen=1.0, roles=[ROLE_RENDEZVOUS])
    (tmp_path / "rv.json").write_text(json.dumps(record), encoding="utf-8")
    (tmp_path / "script.jsonl").write_text(json.dumps({"op": "add_goal", "title": "x"}) + "\n", encoding="utf-8")
    try:
        rc = app_main(["connect", "--identity", str(tmp_path / "app.key"), "--node", files["node"].did,
                       "--rendezvous-record", str(tmp_path / "rv.json"), "--listen", "127.0.0.1:0",
                       "--headless", str(tmp_path / "script.jsonl")])
        assert rc == 1 and "not registered at any rendezvous" in capsys.readouterr().err
    finally:
        channel.close()


def test_a_bootstrap_record_can_be_a_whole_ready_line(files, tmp_path, capsys, monkeypatch):
    """``--bootstrap-record`` takes a record, or the ready line another node
    printed; one that is not a mesh node's is refused before starting."""
    from secdogie_node.node import Node
    from secdogie_transport import Endpoint
    from secdogie_transport.membership import sign_record

    def must_not_start(self):
        raise AssertionError("the node started with a bootstrap record it should have refused")

    monkeypatch.setattr(Node, "start", must_not_start)

    stranger = Identity.generate()
    line = {"event": "ready", "did": stranger.did, "listen": "127.0.0.1:9",
            "record": sign_record(stranger, [Endpoint("local", "127.0.0.1", 9)], last_seen=1.0)}
    (tmp_path / "peer.json").write_text(json.dumps(line), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        main([*files["args"], "--bootstrap-record", str(tmp_path / "peer.json")])
    assert e.value.code == 2 and "bootstrap record" in capsys.readouterr().err
    (tmp_path / "junk.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(SystemExit):
        main([*files["args"], "--bootstrap-record", str(tmp_path / "junk.json")])


def test_a_headless_node_says_so_in_its_ready_line(files):
    from secdogie_transport.membership import verify_record

    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "-m", "secdogie_node.cli", *files["args"], "--device-class", "headless"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        ready = json.loads(proc.stdout.readline())
        rec = verify_record(ready["record"], allowlist=Allowlist({files["node"].did}))
        assert rec.device_class == "headless"
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
