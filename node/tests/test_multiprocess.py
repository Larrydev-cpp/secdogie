"""The whole loop across three OS processes, over real UDP on 127.0.0.1:

  * ``secdogie-relay``, the headless relay;
  * the node -- the real ``secdogie-node`` command, with the scripted model and
    the fake desktop patched in by ``fake_desk_node.py``;
  * the operator App -- ``secdogie-dialogue connect --headless``, signing with
    an encrypted operator keystore.

The App is given a dead address for the node, so every frame between them goes
through the relay. The operator script and the checks are the in-process
test's: Gate 2 signed step, ask_user answered, remember confirmed into S3, the
structural view, and a known failure refused on the fourth run.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_agent")
pytest.importorskip("secdogie_transport")

import fakes  # noqa: E402
from nacl import pwhash  # noqa: E402
from secdogie_citadel.consolidate import build_memory  # noqa: E402
from secdogie_citadel.episodes import episodes_from_events  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_dialogue.keystore import seal_identity  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_identity.capability import create_capability  # noqa: E402

HERE = Path(__file__).parent
FAST = {"opslimit": pwhash.argon2id.OPSLIMIT_MIN, "memlimit": pwhash.argon2id.MEMLIMIT_MIN}


def _allow(path: Path, *dids) -> str:
    path.write_text("".join(f"authorized_did = {d}\n" for d in dids), encoding="utf-8")
    return str(path)


def _start(args, env):
    return subprocess.Popen([sys.executable, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)


def _stop(proc) -> int:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            return proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return proc.returncode


def test_the_loop_across_three_processes_through_a_relay(tmp_path):
    node, app, operator, issuer, relay = (Identity.generate() for _ in range(5))
    for name, ident in (("node", node), ("app", app), ("relay", relay)):
        ident.save(tmp_path / f"{name}.key")
    seal_identity(operator, b"s3cret", tmp_path / "op.keystore", **FAST)
    (tmp_path / "pw").write_bytes(b"s3cret\n")
    journal_path = tmp_path / "node.db"
    j = Journal(str(journal_path), identity=node, allowlist=Allowlist({node.did}))
    j.append("capability_grant", create_capability(issuer, node.did, ["physical.click", "physical.key"], ttl=3600))
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.update(PYTHONUNBUFFERED="1", HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path), APPDATA=str(tmp_path))
    dead = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dead.bind(("127.0.0.1", 0))  # where the App thinks the node is: nothing reads here

    relay_p = _start(["-m", "secdogie_transport.relay_node", "--identity", str(tmp_path / "relay.key"),
                      "--authorized", _allow(tmp_path / "mesh.allow", node.did, app.did),
                      "--listen", "127.0.0.1:0"], env)
    node_p = None
    try:
        record = relay_p.stdout.readline()
        (tmp_path / "relay.json").write_text(record, encoding="utf-8")
        node_p = _start([str(HERE / "fake_desk_node.py"), "run", "--identity", str(tmp_path / "node.key"),
                         "--apps", _allow(tmp_path / "apps.allow", app.did),
                         "--operators", _allow(tmp_path / "ops.allow", operator.did),
                         "--authorized", _allow(tmp_path / "nodes.allow", node.did),
                         "--issuers", _allow(tmp_path / "issuers.allow", issuer.did),
                         "--journal", str(journal_path), "--listen", "127.0.0.1:0",
                         "--relay-record", str(tmp_path / "relay.json")], env)
        ready = json.loads(node_p.stdout.readline())
        assert ready["did"] == node.did
        script = tmp_path / "operator.jsonl"
        script.write_text("\n".join(json.dumps(s) for s in fakes.OPERATOR_SCRIPT) + "\n", encoding="utf-8")
        app_run = subprocess.run(
            [sys.executable, "-m", "secdogie_dialogue.cli", "connect", "--identity", str(tmp_path / "app.key"),
             "--node", node.did, "--node-addr", f"127.0.0.1:{dead.getsockname()[1]}",
             "--listen", "127.0.0.1:0", "--relay-record", str(tmp_path / "relay.json"),
             "--operator-keystore", str(tmp_path / "op.keystore"), "--passphrase-file", str(tmp_path / "pw"),
             "--headless", str(script), "--step-timeout", "30"],
            capture_output=True, text=True, env=env, timeout=240)
        results = [json.loads(line) for line in app_run.stdout.splitlines()]
        assert app_run.returncode == 0, (results, app_run.stderr[-3000:])
        assert [r["op"] for r in results] == [s["op"] for s in fakes.OPERATOR_SCRIPT]
        assert _stop(node_p) == 0
    finally:
        if node_p is not None:
            _stop(node_p)
        _stop(relay_p)
        dead.close()

    events = Journal(str(journal_path), allowlist=Allowlist({node.did})).events()
    episodes = {e.goal_id: e for e in episodes_from_events(events).values()}
    assert episodes["g1"].steps[1].outcome == "ok"  # the Gate 2-signed delete ran
    asks = [e["body"] for e in events if e["kind"] == "ask_result"]
    assert asks == [{"goal_id": "g1", "answered": True, "answer": "Desktop"}]
    fact = build_memory(events, trust=Allowlist({node.did}), confirmers=Allowlist({app.did})).facts()
    assert fact[("global", "report-folder")].value == "reports go to ~/Reports"
    assert [episodes[g].steps[0].outcome for g in ("g2", "g3", "g4")] == ["failed"] * 3
    refused = episodes["g5"].steps[0]
    assert refused.outcome == "rejected" and any("known-failure" in f for f in refused.findings)
