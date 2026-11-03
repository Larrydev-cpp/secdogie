"""`secdogie-inference run` as real processes: nodes that know only a rendezvous
record find each other, link up (discovery), lease a key, and answer a client's
`secdogie-inference ask` end to end; a node is then killed and the mesh keeps
answering."""
from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_transport")

from nacl.public import PrivateKey  # noqa: E402
from secdogie_identity import Identity, create_binding  # noqa: E402

TESTS = Path(__file__).resolve().parent
HEARTBEAT = "0.3"


class Proc:
    """A child process whose stdout / stderr lines are collected on threads."""

    def __init__(self, argv, env):
        self.p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.out: queue.Queue = queue.Queue()
        self.err: list[dict] = []
        threading.Thread(target=self._pump, args=(self.p.stdout, self.out.put), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.p.stderr, self._keep), daemon=True).start()

    @staticmethod
    def _pump(stream, sink):
        for line in stream:
            sink(line)

    def _keep(self, line):
        try:
            self.err.append(json.loads(line))
        except ValueError:
            self.err.append({"raw": line.rstrip()})

    def first_line(self, timeout=20.0) -> dict:
        return json.loads(self.out.get(timeout=timeout))

    def stats(self) -> list[dict]:
        return [e for e in self.err if e.get("event") == "stats"]

    def stop(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                self.p.kill()


def allow(path: Path, dids) -> str:
    path.write_text("".join(f"authorized_did = {d}\n" for d in sorted(dids)), encoding="utf-8")
    return str(path)


@pytest.fixture
def mesh(tmp_path):
    names = ("R", "C", "K", "S1", "S2", "F", "P", "U", "O")
    ids = {n: Identity.generate() for n in names}
    did = {n: i.did for n, i in ids.items()}
    keys = {}
    for n, ident in ids.items():
        keys[n] = str(tmp_path / f"{n}.key")
        ident.save(keys[n])
    trust = tmp_path / "trust"
    trust.mkdir()
    lists = {"mesh": ("C", "K", "S1", "S2", "F", "P"), "users": ("U",), "deciders": ("S1", "S2"),
             "coordinators": ("C",), "brokers": ("K",), "operators": ("O",)}
    for name, members in lists.items():
        allow(trust / f"{name}.allow", [did[m] for m in members])
    lease = PrivateKey.generate()
    lease_file = tmp_path / "P.lease"
    lease_file.write_text(f"private_key = {base64.b64encode(bytes(lease)).decode()}\n", encoding="utf-8")
    binding = create_binding(ids["P"], base64.b64encode(bytes(lease.public_key)).decode(), key_version=1)
    (tmp_path / "keys.json").write_text(json.dumps({"m/a": "sk-test"}), encoding="utf-8")
    (tmp_path / "assignments.json").write_text(json.dumps({"models": {did["P"]: ["m/a"]}, "bindings": [binding]}),
                                               encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(TESTS), os.environ.get("PYTHONPATH", "")])}
    procs: dict[str, Proc] = {}

    record = tmp_path / "rendezvous.json"
    procs["R"] = Proc([sys.executable, "-m", "secdogie_transport.relay_node", "--identity", keys["R"],
                       "--authorized", allow(tmp_path / "relay.allow", [did[n] for n in names if n != "R"]),
                       "--listen", "127.0.0.1:0", "--rendezvous", "--record-out", str(record),
                       "--stats-every", "60"], env)
    procs["R"].first_line()

    common = ["--trust-dir", str(trust), "--listen", "127.0.0.1:0", "--bootstrap", str(record),
              "--heartbeat-every", HEARTBEAT]
    node_args = {
        "C": ["--role", "coordinator", "--standby", did["F"]],
        "K": ["--role", "broker", "--keys", str(tmp_path / "keys.json"),
              "--assignments", str(tmp_path / "assignments.json")],
        "S1": ["--role", "serve", "--role", "forward"],
        "S2": ["--role", "serve", "--role", "forward"],
        "F": ["--role", "forward"],
        "P": ["--role", "model", "--lease-key", str(lease_file), "--backend", "sim:echo"],
    }
    for n, extra in node_args.items():
        procs[n] = Proc([sys.executable, "-m", "secdogie_inference.cli", "run", "--identity", keys[n], *common,
                         "--min-degree", "2", "--stats-every", "1", *extra], env)
    for n in node_args:
        ready = procs[n].first_line()
        assert ready["event"] == "ready" and ready["did"] == did[n]

    def ask(text: str, timeout: float = 60.0) -> dict:
        deadline = time.time() + timeout
        while True:
            r = subprocess.run([sys.executable, "-m", "secdogie_inference.cli", "ask", "--identity", keys["U"],
                                *common, "--entry", did["S1"], "--entry", did["S2"], "--model", "m/a",
                                "--timeout", "25", text], capture_output=True, text=True, env=env, timeout=60)
            out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"status": "none"}
            if out["status"] == "accepted" or time.time() > deadline:
                return out
            time.sleep(1.0)  # the mesh may still be converging (links, the lease)

    try:
        yield did, procs, ask
    finally:
        for p in procs.values():
            p.stop()


def test_nodes_that_know_only_a_rendezvous_find_each_other_and_answer(mesh):
    did, procs, ask = mesh
    out = ask("hello")
    assert out["status"] == "accepted" and out["output"] == "answer(hello)"
    assert out["providers"] == [did["P"]]
    # discovery links every node to at least min_degree transit peers
    deadline = time.time() + 30
    for n in ("S1", "S2", "P", "K"):
        while max((s["degree"] for s in procs[n].stats()), default=0) < 2 and time.time() < deadline:
            time.sleep(0.5)
        assert max((s["degree"] for s in procs[n].stats()), default=0) >= 2, (n, procs[n].stats()[-3:])

    procs["F"].stop()  # a forwarder disappears; the mesh routes on
    out = ask("still there?")
    assert out["status"] == "accepted" and out["output"] == "answer(still there?)"
