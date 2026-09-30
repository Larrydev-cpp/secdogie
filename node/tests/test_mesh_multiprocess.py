"""Stage 3's end-to-end check: the mesh across five kinds of OS process, over real
UDP on 127.0.0.1.

  * ``secdogie-relay --rendezvous`` -- the directory every node registers with;
  * node A and node B -- the real ``secdogie-node`` with the scripted model and
    the fake desktop (``fake_desk_node.py``), display nodes;
  * node H -- the real ``secdogie-node --device-class headless``;
  * the operator App -- ``secdogie-dialogue connect --headless``, given only a
    node's DID and the rendezvous record, never an address.

B and H start from A's ready line (``--bootstrap-record``) and find the rest by
gossip. In order:

  1. A tidy-up click fails in three of A's goals; A's journal replicates, and B
     -- which never failed at it -- refuses the same click at Gate 1 on its
     very first goal (``known-failure``).
  2. The App asks the headless node H for a goal and is refused.
  3. With B stopped, the operator revokes H through A's revocation store: A
     applies it, journals it and floods it; H, revoked, halts by itself.
  4. B comes back with the journal it had, catches up from A's, and applies
     the revocation it was not there to see.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_agent")
pytest.importorskip("secdogie_transport")

from secdogie_citadel.consolidate import build_memory  # noqa: E402
from secdogie_citadel.episodes import episodes_from_events  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.revocations import revocation_events  # noqa: E402
from secdogie_identity import Allowlist, Identity, cosign, create_revocation  # noqa: E402
from secdogie_identity.capability import create_capability  # noqa: E402

HERE = Path(__file__).parent


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


def _wait(pred, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001 - a journal mid-write reads as "not yet"
            pass
        time.sleep(0.2)
    return pred()


def test_the_mesh_across_five_processes(tmp_path):
    a, b, h, app, operator, issuer, rv, master = (Identity.generate() for _ in range(8))
    for name, ident in (("a", a), ("b", b), ("h", h), ("app", app), ("rv", rv)):
        ident.save(tmp_path / f"{name}.key")
    nodes = Allowlist({a.did, b.did, h.did})
    for name, ident in (("a", a), ("b", b)):  # the operator lets A and B click
        j = Journal(str(tmp_path / f"{name}.db"), identity=ident, allowlist=nodes)
        j.append("capability_grant", create_capability(issuer, ident.did, ["physical.click"], ttl=3600))
    (tmp_path / "masters.conf").write_text(f"master_did = {master.did}\nthreshold = 1\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.update(PYTHONUNBUFFERED="1", HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path), APPDATA=str(tmp_path),
               SECDOGIE_FAKE_PLAN="tidy")

    def node_args(name, ident, *extra):
        return ["run", "--identity", str(tmp_path / f"{name}.key"),
                "--apps", _allow(tmp_path / "apps.allow", app.did),
                "--operators", _allow(tmp_path / "ops.allow", operator.did),
                "--authorized", _allow(tmp_path / "nodes.allow", *nodes.dids()),
                "--mesh", str(tmp_path / "nodes.allow"),
                "--issuers", _allow(tmp_path / "issuers.allow", issuer.did),
                "--masters", str(tmp_path / "masters.conf"), "--revocations", str(tmp_path / f"{name}.rev"),
                "--journal", str(tmp_path / f"{name}.db"), "--listen", "127.0.0.1:0",
                "--rendezvous-record", str(tmp_path / "rv.json"), "--mesh-every", "0.3", *extra]

    def app_run(ident_did, steps):
        script = tmp_path / f"script-{len(list(tmp_path.glob('script-*')))}.jsonl"
        script.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")
        run = subprocess.run(
            [sys.executable, "-m", "secdogie_dialogue.cli", "connect", "--identity", str(tmp_path / "app.key"),
             "--node", ident_did, "--rendezvous-record", str(tmp_path / "rv.json"), "--listen", "127.0.0.1:0",
             "--headless", str(script), "--step-timeout", "40"],
            capture_output=True, text=True, env=env, timeout=240)
        return run.returncode, [json.loads(line) for line in run.stdout.splitlines()], run.stderr

    def events(name):
        return Journal(str(tmp_path / f"{name}.db"), allowlist=nodes).events()

    procs = {}
    try:
        procs["rv"] = _start(["-m", "secdogie_transport.relay_node", "--identity", str(tmp_path / "rv.key"),
                              "--authorized", _allow(tmp_path / "mesh.allow", a.did, b.did, h.did, app.did),
                              "--listen", "127.0.0.1:0", "--rendezvous"], env)
        (tmp_path / "rv.json").write_text(procs["rv"].stdout.readline(), encoding="utf-8")
        procs["a"] = _start([str(HERE / "fake_desk_node.py"), *node_args("a", a)], env)
        (tmp_path / "a.ready").write_text(procs["a"].stdout.readline(), encoding="utf-8")
        boot = ["--bootstrap-record", str(tmp_path / "a.ready")]
        procs["b"] = _start([str(HERE / "fake_desk_node.py"), *node_args("b", b, *boot)], env)
        procs["h"] = _start(["-m", "secdogie_node.cli", *node_args("h", h, *boot, "--device-class", "headless")], env)
        for name in ("b", "h"):
            assert json.loads(procs[name].stdout.readline())["event"] == "ready"

        # 1. A fails the click three times ...
        rc, results, err = app_run(a.did, [
            *({"op": "add_goal", "title": "tidy up", "goal_id": f"g{i}"} for i in range(3)),
            {"op": "expect_status", "match": "goal g2 finished"}])
        assert rc == 0, (results, err[-3000:])
        # ... and B learns it without ever failing
        assert _wait(lambda: build_memory(events("b"), trust=nodes).known_failures)
        rc, results, err = app_run(b.did, [{"op": "add_goal", "title": "tidy up", "goal_id": "h1"},
                                           {"op": "expect_status", "match": "goal h1 finished"}])
        assert rc == 0, (results, err[-3000:])

        # 2. the headless node takes no goal
        rc, results, _ = app_run(h.did, [{"op": "add_goal", "title": "tidy up", "goal_id": "x1"}])
        assert rc != 0 and "headless node" in results[-1]["error"]

        # 3. B goes away; the operator revokes H through A
        assert _stop(procs["b"]) == 0
        record = cosign(master, create_revocation([h.did], reason="decommissioned"))
        with open(tmp_path / "a.rev", "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        assert _wait(lambda: revocation_events(events("a")))
        assert procs["h"].wait(timeout=60) == 0  # H learned it was revoked, and halted cleanly
        assert "revoked" in procs["h"].stderr.read()

        # 4. B returns with its old journal and catches up
        procs["b"] = _start([str(HERE / "fake_desk_node.py"), *node_args("b", b, *boot)], env)
        assert json.loads(procs["b"].stdout.readline())["event"] == "ready"
        assert _wait(lambda: record["record_id"] in (tmp_path / "b.rev").read_text(encoding="utf-8"))
        assert _wait(lambda: revocation_events(events("b")))
        for name in ("b", "a", "rv"):
            assert _stop(procs[name]) == 0, procs[name].stderr.read()[-3000:]
    finally:
        for proc in procs.values():
            _stop(proc)

    a_runs = {e.goal_id: e for e in episodes_from_events(events("a")).values() if e.goal_id.startswith("g")}
    assert [a_runs[f"g{i}"].steps[0].outcome for i in range(3)] == ["failed"] * 3
    b_events = events("b")
    b_runs = [e for e in episodes_from_events(b_events).values() if e.goal_id == "h1"]
    (h1,) = b_runs
    first = h1.steps[0]
    assert first.outcome == "rejected" and any("known-failure" in f for f in first.findings)
    assert not [e for e in b_events if e.get("author") == b.did and e.get("kind") == "goal"
                and (e.get("body") or {}).get("id", "").startswith("g")]  # B ran none of A's goals
