"""The operator page's own wire stack (symbiont/src/net, TypeScript) against a
real Python node over UDP: HELLO, CURRENT_STATUS, ADD_GOAL, a question
answered, a Gate 2 challenge signed in TypeScript and verified by the node's
gate, the goal reported done. Needs Node 22.18+ (it runs the .ts directly);
in CI a missing Node is a failure, not a skip."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[1] / "symbiont" / "tests" / "interop" / "app_udp.ts"
DELETE = {"kind": "key", "element": None, "x": None, "y": None, "text": "", "keys": ["delete"], "path": "",
          "high_risk": True, "rollback": "", "irreversible": True}


def _node_ok() -> bool:
    exe = shutil.which("node")
    if not exe:
        return False
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    major, minor = (int(x) for x in out.split(".")[:2])
    return (major, minor) >= (22, 18)


if not _node_ok():
    if os.environ.get("CI"):
        pytest.fail("the interop test needs Node 22.18+ in CI", pytrace=False)
    pytest.skip("needs Node 22.18+ to run the TypeScript stack", allow_module_level=True)


def _identity(label: str) -> tuple[Identity, str]:
    seed = hashlib.sha256(f"secdogie/interop/{label}".encode()).digest()
    return Identity.from_seed_b64(base64.b64encode(seed).decode()), seed.hex()


def test_the_page_stack_runs_a_goal_against_a_python_node():
    node_id, _ = _identity("node")
    app, app_seed = _identity("app")
    operator, op_seed = _identity("operator")
    seen = {}

    def task(title, *, should_stop, on_status, confirm, record_step=None, plan_gate=None, ask=None, **_):
        seen["title"] = title
        seen["answer"] = ask("Which folder should old files go to?")
        allowed, note = plan_gate(DELETE, [])
        if not allowed:
            return 1, f"refused: {note}"
        return (0, f"tidied into {seen['answer']}") if confirm("Execute HIGH-RISK key(delete)?", True) else (1, "no")

    node = Node(NodeConfig(identity=node_id, apps=Allowlist({app.did}), operators=Allowlist({operator.did}),
                           authorized=Allowlist({node_id.did}), unrestricted=True, run_task=task, idle_poll=0.05,
                           listen=("127.0.0.1", 0)))
    node.start()
    try:
        host, port = node.address
        cfg = {"node_host": host, "node_port": port, "node_did": node_id.did, "app_seed_hex": app_seed,
               "operator_seed_hex": op_seed, "answer": "Downloads"}
        proc = subprocess.run(["node", str(HARNESS)], input=json.dumps(cfg), capture_output=True, text=True,
                              timeout=90, cwd=HARNESS.parents[2])
    finally:
        node.stop()
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    assert proc.returncode == 0, (events, proc.stderr[-3000:])
    kinds = [e["event"] for e in events]
    assert kinds[0] == "hello-sent"
    statuses = [e["content"] for e in events if e["event"] == "status"]
    assert statuses[0] == "status: idle"  # CURRENT_STATUS, first thing after HELLO
    assert "accepted: goal g-1 queued" in statuses
    assert "status: running" in statuses
    assert "challenge" in kinds and "question" in kinds
    assert seen == {"title": "清理旧文件 — tidy old files", "answer": "Downloads"}
    (done,) = [e for e in events if e["event"] == "done"]
    assert done["content"].startswith("goal g-1 finished: exit 0")
