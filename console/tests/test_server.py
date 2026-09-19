"""End-to-end HTTP tests: a real console server on an ephemeral port, driven
with urllib against a fake fleet. Proves routing, the state endpoint, and the
operator-DID gate on /api/command."""
import json
import threading
import urllib.error
import urllib.request

import pytest

pytest.importorskip("nacl")

from secdogie_console.controller import ConsoleController  # noqa: E402
from secdogie_console.server import build_server  # noqa: E402
from secdogie_identity import Allowlist, Identity, sign_payload  # noqa: E402
from test_controller import FakeFleet  # noqa: E402


def _serve(controller):
    server = build_server(controller, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, thread, f"http://{host}:{port}"


def _get(base, path):
    resp = urllib.request.urlopen(base + path, timeout=2)
    return resp.status, json.loads(resp.read())


def _post(base, path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=2)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def dev_server():
    server, thread, base = _serve(ConsoleController(FakeFleet()))  # loopback dev mode
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_index_and_state(dev_server):
    resp = urllib.request.urlopen(dev_server + "/", timeout=2)
    assert resp.status == 200 and "text/html" in resp.headers.get("Content-Type")
    status, state = _get(dev_server, "/api/state")
    assert status == 200
    assert state["requires_signature"] is False
    assert "nodes" in state and "tasks" in state


def test_dev_mode_submit_works_unsigned(dev_server):
    status, out = _post(dev_server, "/api/command", {"op": "submit", "task": "tidy"})
    assert status == 200 and out["task_id"] == "t-1"


def test_bad_op_is_400(dev_server):
    status, out = _post(dev_server, "/api/command", {"op": "nope"})
    assert status == 400 and "error" in out


def test_signed_mode_rejects_unsigned_and_accepts_signed():
    operator = Identity.generate()
    controller = ConsoleController(FakeFleet(), operator_allowlist=Allowlist({operator.did}))
    server, thread, base = _serve(controller)
    try:
        status, _state = _get(base, "/api/state")
        assert status == 200

        # unsigned command -> 403
        status, out = _post(base, "/api/command", {"op": "submit", "task": "x"})
        assert status == 403 and "unauthorized" in out["error"]

        # operator-signed command -> 200
        signed = sign_payload(operator, {"op": "submit", "task": "x", "options": {"auto": True}})
        status, out = _post(base, "/api/command", signed)
        assert status == 200 and out["task_id"] == "t-1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
