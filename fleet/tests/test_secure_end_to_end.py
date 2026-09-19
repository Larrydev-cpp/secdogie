"""End-to-end over real sockets, with DID signing on: an authorized node
registers and runs a task; an unauthorized DID is refused; a different DID
cannot hijack an established node_id; and a tampered/unsigned line is dropped
without killing the connection.

Mirrors test_end_to_end.py's real-socket + faked-task-runner idiom, adding the
secure path (secdogie-identity). Requires pynacl (the fleet[secure] extra)."""
import socket
import threading
import time

import pytest

pytest.importorskip("nacl")  # skip cleanly where the secure extra isn't installed

from secdogie_fleet import node as node_mod  # noqa: E402
from secdogie_fleet.protocol import Hello, to_json  # noqa: E402
from secdogie_fleet.secure import signed_to_json  # noqa: E402
from secdogie_fleet.server import FleetServer  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402


def _wait(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _node_ids(server):
    return {n["node_id"] for n in server.snapshot()["nodes"]}


def _task(server, task_id):
    return next(t for t in server.snapshot()["tasks"] if t["task_id"] == task_id)


@pytest.fixture
def keys():
    coord = Identity.generate()
    node_a = Identity.generate()
    node_b = Identity.generate()
    stranger = Identity.generate()
    # The coordinator authorizes node A and node B (both self-owned); the node
    # side authorizes the coordinator.
    node_allow = Allowlist({node_a.did, node_b.did})
    coord_allow = Allowlist({coord.did})
    return dict(coord=coord, node_a=node_a, node_b=node_b, stranger=stranger,
                node_allow=node_allow, coord_allow=coord_allow)


@pytest.fixture
def server(keys):
    s = FleetServer(host="127.0.0.1", port=0, signer=keys["coord"], node_allowlist=keys["node_allow"])
    s.start()
    yield s
    s.shutdown()


def _spawn_secure_node(server, node_id, identity, run_task, coord_allow, label=""):
    host, port = server.address
    t = threading.Thread(
        target=lambda: node_mod.connect_and_serve(
            host, port, node_id=node_id, label=label, run_task=run_task,
            identity=identity, coordinator_allowlist=coord_allow,
        ),
        daemon=True, name=f"test-secure-node-{node_id}",
    )
    t.start()
    return t


def test_authorized_node_registers_and_runs(server, keys):
    ran = threading.Event()

    def run_task(task, options, should_stop, on_progress):
        on_progress(1, "working")
        ran.set()
        return 0, f"finished: {task}"

    _spawn_secure_node(server, "n1", keys["node_a"], run_task, keys["coord_allow"], label="vm-1")
    assert _wait(lambda: "n1" in _node_ids(server)), "authorized node never registered"

    tid = server.submit("tidy the desktop", {"auto": True})
    assert _wait(ran.is_set), "signed assign never reached the node"
    assert _wait(lambda: _task(server, tid)["state"] == "done")
    assert _task(server, tid)["summary"] == "finished: tidy the desktop"


def test_unauthorized_did_is_refused(server, keys):
    def run_task(task, options, should_stop, on_progress):
        return 0, "ok"

    # `stranger` holds a valid key but is not on the allowlist.
    _spawn_secure_node(server, "nbad", keys["stranger"], run_task, keys["coord_allow"])
    # It must never enter the fleet, no matter how long we wait.
    assert not _wait(lambda: "nbad" in _node_ids(server), timeout=1.0), \
        "an unauthorized DID was allowed to register"


def test_different_did_cannot_hijack_node_id(server, keys):
    ran_a = threading.Event()

    def run_task(task, options, should_stop, on_progress):
        ran_a.set()
        return 0, "ran on A"

    # The real node A registers as n1.
    _spawn_secure_node(server, "n1", keys["node_a"], run_task, keys["coord_allow"])
    assert _wait(lambda: "n1" in _node_ids(server))

    # Node B is also authorized, but tries to seize n1's slot with its own DID.
    host, port = server.address
    with socket.create_connection((host, port)) as hijacker:
        hijacker.sendall((signed_to_json(Hello(node_id="n1", label="thief"), keys["node_b"]) + "\n").encode())
        time.sleep(0.3)  # let the server process (and refuse) it

        # n1 must still route to A: a submitted task runs on A, and the hijacker
        # never receives the assign.
        server.submit("who am I")
        assert _wait(ran_a.is_set), "the task did not run on the real node A -- hijack may have succeeded"
        hijacker.settimeout(0.5)
        try:
            got = hijacker.recv(4096)
        except TimeoutError:
            got = b""
        assert b"who am I" not in got, "the hijacker received A's assignment"
    assert _node_ids(server) == {"n1"}


def test_tampered_and_unsigned_lines_are_dropped_without_killing_the_connection(server, keys):
    host, port = server.address
    with socket.create_connection((host, port)) as sock:
        # 1) a validly-signed line whose signature is then corrupted
        good = signed_to_json(Hello(node_id="n9", label="early"), keys["node_a"])
        tampered = good.replace('"label":"early"', '"label":"tampered"')
        assert tampered != good
        sock.sendall((tampered + "\n").encode())
        # 2) an unsigned line (no envelope) -- rejected in secure mode
        sock.sendall((to_json(Hello(node_id="n9", label="plain")) + "\n").encode())
        # 3) a clean signed Hello -- must still be accepted
        sock.sendall((signed_to_json(Hello(node_id="n9", label="real"), keys["node_a"]) + "\n").encode())
        assert _wait(lambda: "n9" in _node_ids(server)), "connection died on bad/unsigned input"
