"""The browser link for real: Chromium (Playwright) running the built operator
page, against a real node with its aiortc peer, through the in-process
signaling gateway. Pairing (the same check code on both ends, the owner's
"y", the tap), W1 between Chromium's and aiortc's own fingerprints, a goal,
a question answered from the composer, and a Gate 2 approval made after a
reload in the middle of it -- verified by the node's gate.

Needs: the [webrtc] extra, Node 22 with Playwright (PLAYWRIGHT_MODULE may name
its index.mjs), Chromium, a built page (npm run build), and a non-loopback IPv4
address. With CI set, a missing piece fails rather than skips."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

SYMBIONT = Path(__file__).resolve().parents[2]


def _need(ok: bool, why: str) -> None:
    if not ok:
        if os.environ.get("CI"):
            pytest.fail(f"the browser-link test cannot run: {why}", pytrace=False)
        pytest.skip(why, allow_module_level=True)


_need(shutil.which("node") is not None, "needs Node")
_need((SYMBIONT / "dist" / "ui" / "main.js").exists(), "needs the built page (npm run build)")
for mod in ("nacl", "aiortc", "websockets", "secdogie_node"):
    try:
        __import__(mod)
    except ImportError:
        _need(False, f"needs {mod}")


def _lan() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return not s.getsockname()[0].startswith("127.")
    except OSError:
        return False
    finally:
        s.close()


_need(_lan(), "aiortc's ICE needs a non-loopback IPv4 address")

from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_identity.linkauth import derive_room  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402
from secdogie_node.pairing import PairingOffer, PairingPolicy, file_enroller  # noqa: E402
from secdogie_transport.webrtc import WebRTCChannel, WebRTCConfig  # noqa: E402
from secdogie_transport.webrtc_testing import FakeSignalingServer  # noqa: E402

DELETE = {"kind": "key", "element": None, "x": None, "y": None, "text": "", "keys": ["delete"], "path": "",
          "high_risk": True, "rollback": "", "irreversible": True}


def test_chromium_pairs_attaches_and_approves_across_a_reload(tmp_path):
    server = FakeSignalingServer()
    url = server.start()
    env = {**os.environ, "SECDOGIE_SIGNAL_URL": url}
    subprocess.run(["node", "scripts/site.mjs"], cwd=SYMBIONT, env=env, check=True, capture_output=True)
    serve = subprocess.Popen(["node", "scripts/serve.mjs", "--port", "0"], cwd=SYMBIONT, stdout=subprocess.PIPE,
                             text=True)
    port = int(re.search(r"127\.0\.0\.1:(\d+)/", serve.stdout.readline()).group(1))

    identity = Identity.generate()
    apps_file, ops_file = tmp_path / "apps.allow", tmp_path / "ops.allow"
    apps_file.write_text("", encoding="utf-8")
    ops_file.write_text("", encoding="utf-8")
    apps, ops = Allowlist.load(apps_file), Allowlist.load(ops_file)
    seen = {}

    def task(title, *, should_stop, on_status, confirm, record_step=None, plan_gate=None, ask=None, **_):
        seen["title"] = title
        seen["answer"] = ask("Which folder should the old files go to?")
        allowed, note = plan_gate(DELETE, [])
        seen["allowed"] = allowed
        if not allowed:
            return 1, f"refused: {note}"
        return (0, "tidied") if confirm("Execute HIGH-RISK key(delete)?", True) else (1, "not confirmed")

    room = derive_room(identity)
    node = Node(NodeConfig(identity=identity, apps=apps, operators=ops, authorized=Allowlist({identity.did}),
                           unrestricted=True, run_task=task, idle_poll=0.05, challenge_ttl=90.0, probe_ttl=90.0,
                           webrtc=WebRTCConfig(url, room, ice_servers=()), apps_file=str(apps_file),
                           operators_file=str(ops_file)))
    asked = []
    offer = PairingOffer(ttl=180)
    policy = PairingPolicy(identity, offer, standing_room=room, ask=lambda q: asked.append(q) or True,
                           enroll=file_enroller(str(apps_file), str(ops_file), pairing_id=offer.pairing_id))
    pairing = WebRTCChannel(WebRTCConfig(url, offer.room, ice_servers=(), bind_timeout=180), policy)
    shots = Path(os.environ.get("SECDOGIE_SHOTS", tmp_path))
    try:
        node.start()
        pairing.start(lambda d, i: None)
        pairing.open()
        deadline = time.monotonic() + 10
        while server.peers(room) < 1 or server.peers(offer.room) < 1:
            assert time.monotonic() < deadline, "the node did not join its rooms"
            time.sleep(0.05)
        link = offer.link(f"http://127.0.0.1:{port}/", identity.did)
        drive = subprocess.run(["node", "tests/browser/drive_link.mjs", json.dumps({"url": link, "shots": str(shots)})],
                               cwd=SYMBIONT, capture_output=True, text=True, timeout=240, env=os.environ)
    finally:
        pairing.close()
        node.stop()
        serve.terminate()
        server.stop()
    events = [json.loads(line) for line in drive.stdout.splitlines() if line.startswith("{")]
    by = {e["event"]: e for e in events}
    assert drive.returncode == 0 and "failure" not in by, (events, drive.stderr[-3000:])
    # pairing: the code on the page is the code the terminal asked about; the link left the address bar
    assert by["code"]["code"] in asked[0] and "#pair" not in by["code"]["url_after"]
    assert policy.result is not None and apps.contains(policy.result.app_did) and ops.contains(policy.result.operator_did)
    # attached, asked, consented across a reload, done -- and the node's gate accepted the signature
    assert by["connected"]["label"] == "共生体: 已连接"
    assert seen == {"title": "把旧文件整理一下", "answer": "Downloads", "allowed": True}
    assert by["consent"]["text"] == by["consent-again"]["text"] == "这一步会按下 Delete，做完就没法恢复了。确认要继续吗？"
    assert "state-done" in by["done"]["consent"]
    page = by["page"]
    assert page["problems"] == [] and page["traced"] > 0
    assert identity.did not in page["text"] and policy.result.app_did not in page["text"]
