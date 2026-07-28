"""End-to-end over real sockets: a FleetServer plus real node connections,
with only the *task runner* faked (running a real agent needs a desktop and a
model). This is what proves the pieces actually fit -- protocol, transport,
scheduling, and a node dropping mid-task -- rather than each half being tested
against its own assumptions."""
import threading
import time

import pytest
from secdogie_fleet import node as node_mod
from secdogie_fleet.server import FleetServer


@pytest.fixture
def server():
    s = FleetServer(host="127.0.0.1", port=0)  # port 0 = let the OS pick a free one
    s.start()
    yield s
    s.shutdown()


def _spawn_node(server, node_id, run_task, label=""):
    """Run a real node against the server on a background thread."""
    host, port = server.address
    t = threading.Thread(
        target=lambda: node_mod.connect_and_serve(
            host, port, node_id=node_id, label=label, run_task=run_task
        ),
        daemon=True, name=f"test-node-{node_id}",
    )
    t.start()
    return t


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


def test_a_node_registers_and_runs_a_submitted_task(server):
    ran = threading.Event()

    def run_task(task, options, should_stop, on_progress):
        on_progress(1, "working")
        ran.set()
        return 0, f"finished: {task}"

    _spawn_node(server, "n1", run_task, label="vm-1")
    assert _wait(lambda: "n1" in _node_ids(server)), "node never registered"

    tid = server.submit("tidy the desktop", {"auto": True})
    assert _wait(ran.is_set), "task never reached the node"
    assert _wait(lambda: _task(server, tid)["state"] == "done")
    assert _task(server, tid)["summary"] == "finished: tidy the desktop"


def test_two_nodes_run_two_tasks_at_the_same_time(server):
    """The whole point of the fleet: both desktops act concurrently. Each task
    blocks until BOTH have started, so it can only pass if they truly overlap."""
    both_started = threading.Barrier(2, timeout=5)
    started = []

    def run_task(task, options, should_stop, on_progress):
        started.append(task)
        both_started.wait()      # deadlocks (and fails) if runs are serialized
        return 0, "ok"

    _spawn_node(server, "n1", run_task, label="vm-1")
    _spawn_node(server, "n2", run_task, label="vm-2")
    assert _wait(lambda: _node_ids(server) == {"n1", "n2"}), "both nodes never registered"

    t1 = server.submit("task one")
    t2 = server.submit("task two")

    assert _wait(lambda: _task(server, t1)["state"] == "done" and _task(server, t2)["state"] == "done"), \
        "tasks did not both complete -- they may have been serialized"
    assert sorted(started) == ["task one", "task two"]


def test_a_task_waits_when_every_desktop_is_busy(server):
    release = threading.Event()

    def run_task(task, options, should_stop, on_progress):
        release.wait(5)
        return 0, "ok"

    _spawn_node(server, "n1", run_task)
    assert _wait(lambda: "n1" in _node_ids(server))

    t1 = server.submit("first")
    t2 = server.submit("second")
    assert _wait(lambda: _task(server, t1)["state"] == "running")
    assert _task(server, t2)["state"] == "queued"     # one desktop, one at a time

    release.set()
    assert _wait(lambda: _task(server, t2)["state"] == "done", timeout=8)


def test_a_node_that_disappears_hands_its_task_to_another_desktop(server):
    """A dead VM must not take the work with it.

    The doomed node is a raw socket rather than a real node, so the drop is the
    one a crashed guest actually produces: the *peer* vanishes, which is what
    the server detects (recv returning empty).
    """
    import socket

    from secdogie_fleet import protocol as p

    def healthy(task, options, should_stop, on_progress):
        return 0, "rescued"

    host, port = server.address
    doomed = socket.create_connection((host, port))
    doomed.sendall((p.to_json(p.Hello(node_id="n1", label="doomed")) + "\n").encode())
    assert _wait(lambda: "n1" in _node_ids(server))

    tid = server.submit("important work")
    # It should be handed to n1 -- read the assign off the wire to be sure.
    doomed.settimeout(5)
    assert b"important work" in doomed.recv(4096), "the task never reached n1"
    assert _task(server, tid)["node_id"] == "n1"

    doomed.close()  # the guest crashes
    assert _wait(lambda: "n1" not in _node_ids(server)), "server never noticed the drop"

    _spawn_node(server, "n2", healthy, label="rescuer")
    assert _wait(lambda: _task(server, tid)["state"] == "done", timeout=8), \
        "the requeued task never ran elsewhere"
    assert _task(server, tid)["summary"] == "rescued"
    assert _task(server, tid)["attempts"] == 2


def test_max_concurrent_holds_the_fleet_below_the_node_count():
    """The binding limit is usually the shared API quota, not the desktops."""
    server = FleetServer(host="127.0.0.1", port=0, max_concurrent=1)
    server.start()
    try:
        release = threading.Event()
        running = []

        def run_task(task, options, should_stop, on_progress):
            running.append(task)
            release.wait(5)
            return 0, "ok"

        _spawn_node(server, "n1", run_task)
        _spawn_node(server, "n2", run_task)
        assert _wait(lambda: _node_ids(server) == {"n1", "n2"})

        server.submit("a")
        server.submit("b")
        assert _wait(lambda: len(running) == 1)
        time.sleep(0.2)
        assert len(running) == 1, "the cap did not hold a second task back"

        release.set()
        assert _wait(lambda: server.snapshot()["settled"], timeout=8)
    finally:
        server.shutdown()


def test_bad_lines_from_a_node_do_not_kill_the_connection(server):
    """A garbled line must be skipped, not take the node down with it."""
    import socket

    from secdogie_fleet import protocol as p

    host, port = server.address
    with socket.create_connection((host, port)) as sock:
        sock.sendall(b"{not json at all}\n")
        sock.sendall(b'{"kind": "nonsense"}\n')
        sock.sendall((p.to_json(p.Hello(node_id="n9", label="late")) + "\n").encode())
        assert _wait(lambda: "n9" in _node_ids(server)), "the connection died on bad input"
