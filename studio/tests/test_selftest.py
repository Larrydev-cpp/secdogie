from __future__ import annotations

import io
import json
import subprocess
import sys

from secdogie_studio.link import check_link
from secdogie_studio.nodeproc import NodeProcess, Ready
from secdogie_studio.selftest import run_self_test


def test_the_self_test_passes_every_step():
    out = io.StringIO()
    assert run_self_test(out=out) == 0
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [x.get("step") for x in lines[:-1]] == ["profile", "node", "link", "stop", "webrtc"]
    assert all(x["ok"] for x in lines[:-1]) and lines[-1] == {"self_test": "passed"}


def test_the_self_test_as_a_process():
    r = subprocess.run([sys.executable, "-m", "secdogie_studio", "--self-test"], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr[-2000:]
    assert json.loads(r.stdout.splitlines()[-1]) == {"self_test": "passed"}


def test_the_link_refuses_a_node_that_is_not_this_profiles(profile, tmp_path):
    from secdogie_studio.profile import create_profile

    other = create_profile(tmp_path / "other", b"another passphrase", kdf="min")
    with NodeProcess(other.node_args()) as node:
        ready = node.wait_ready(30)
        try:
            check_link(profile, ready, timeout=2)
        except RuntimeError as e:
            assert "not this profile's node" in str(e)
        else:
            raise AssertionError("linked to a foreign node")


def test_the_link_reaches_only_a_node_that_trusts_this_app(profile, tmp_path):
    """A node with this profile's key but another App on its allowlist never
    answers: the App's frames are dropped at the node's door."""
    other_app = tmp_path / "other.allow"
    from secdogie_identity import Identity

    other_app.write_text(f"authorized_did = {Identity.generate().did}\n", encoding="utf-8")
    args = profile.node_args()
    args[args.index("--apps") + 1] = str(other_app)
    with NodeProcess(args) as node:
        ready = node.wait_ready(30)
        try:
            check_link(profile, Ready(ready.did, ready.host, ready.port), timeout=2)
        except RuntimeError as e:
            assert "no reply" in str(e) or "never received" in str(e)
        else:
            raise AssertionError("an untrusted App got an answer")
