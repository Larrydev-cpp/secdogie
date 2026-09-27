"""Master revocation, end to end over real sockets (R2).

The coordinator takes a revocation-aware allowlist (secdogie_identity.TrustPolicy):
when a node's DID is revoked, that node is disconnected at once, its task goes
back in the queue for another node, and it cannot come back. A node whose own
DID is revoked stops its task and ends its session, and the CLI exits 0 without
reconnecting."""
import threading
import time

import pytest

pytest.importorskip("nacl")

from secdogie_fleet import cli  # noqa: E402
from secdogie_fleet import node as node_mod  # noqa: E402
from secdogie_fleet.server import FleetServer  # noqa: E402
from secdogie_identity import (  # noqa: E402
    Allowlist,
    Identity,
    MasterSet,
    RevocationStore,
    TrustPolicy,
    cosign,
    create_revocation,
)


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


def _revocation(master, dids):
    return cosign(master, create_revocation(dids))


@pytest.fixture
def mesh():
    master = Identity.generate()
    coord, node_a, node_b = Identity.generate(), Identity.generate(), Identity.generate()
    masters = MasterSet([master.did])
    node_policy = TrustPolicy(Allowlist({node_a.did, node_b.did}), masters=masters)
    coord_allow = Allowlist({coord.did})
    server = FleetServer(host="127.0.0.1", port=0, signer=coord, node_allowlist=node_policy)
    server.start()
    release = threading.Event()  # lets any still-blocked fake task finish at teardown
    try:
        yield dict(master=master, coord=coord, node_a=node_a, node_b=node_b, masters=masters,
                   node_policy=node_policy, coord_allow=coord_allow, server=server, release=release)
    finally:
        release.set()
        server.shutdown()


def _spawn(mesh, node_id, identity, run_task, **kwargs):
    host, port = mesh["server"].address
    t = threading.Thread(
        target=lambda: node_mod.connect_and_serve(
            host, port, node_id=node_id, run_task=run_task, identity=identity,
            coordinator_allowlist=mesh["coord_allow"], **kwargs,
        ),
        daemon=True, name=f"test-revocation-{node_id}",
    )
    t.start()
    return t


def test_a_revoked_node_is_dropped_and_its_task_moves_on(mesh):
    server = mesh["server"]
    started_on_a = threading.Event()

    def blocking(task, options, should_stop, on_progress):
        started_on_a.set()
        mesh["release"].wait(10)
        return 0, "released"

    def quick(task, options, should_stop, on_progress):
        return 0, "done on b"

    _spawn(mesh, "node-a", mesh["node_a"], blocking)
    assert _wait(lambda: "node-a" in _node_ids(server))
    tid = server.submit("tidy up", {})
    assert started_on_a.wait(5)

    _spawn(mesh, "node-b", mesh["node_b"], quick)
    assert _wait(lambda: "node-b" in _node_ids(server))

    # The masters revoke node A. The policy notifies the server, which drops it.
    assert mesh["node_policy"].apply(_revocation(mesh["master"], [mesh["node_a"].did])) == {
        mesh["node_a"].did}
    assert _wait(lambda: "node-a" not in _node_ids(server))
    assert _wait(lambda: _task(server, tid)["state"] == "done")
    task = _task(server, tid)
    assert task["summary"] == "done on b" and task["attempts"] == 2  # moved on to node B


def test_a_revoked_node_cannot_reconnect(mesh):
    server = mesh["server"]
    mesh["node_policy"].apply(_revocation(mesh["master"], [mesh["node_a"].did]))
    _spawn(mesh, "node-a", mesh["node_a"], lambda **kw: (0, ""))
    time.sleep(0.5)
    assert "node-a" not in _node_ids(server)  # its hello no longer verifies
    _spawn(mesh, "node-b", mesh["node_b"], lambda **kw: (0, ""))
    assert _wait(lambda: "node-b" in _node_ids(server))  # others are unaffected


def test_a_node_asked_to_stop_ends_its_task_and_session(mesh):
    server = mesh["server"]
    saw_stop = threading.Event()

    def until_stopped(task, options, should_stop, on_progress):
        while not should_stop():
            if mesh["release"].wait(0.02):
                return 1, "not stopped"
        saw_stop.set()
        return 5, "stopped"

    stop = threading.Event()
    thread = _spawn(mesh, "node-a", mesh["node_a"], until_stopped, stop_event=stop, stop_grace=5.0)
    assert _wait(lambda: "node-a" in _node_ids(server))
    tid = server.submit("long job", {})
    assert _wait(lambda: _task(server, tid)["state"] == "running")

    stop.set()
    thread.join(5)
    assert not thread.is_alive()           # the session ended
    assert saw_stop.is_set()               # the task was told to stop
    assert _wait(lambda: "node-a" not in _node_ids(server))


# --- the CLI ----------------------------------------------------------------


def _files(tmp_path, node, coord, master, revoked=()):
    key = tmp_path / "node.key"
    node.save(key)
    allow = tmp_path / "coordinators.allow"
    allow.write_text(f"authorized_did = {coord.did}\n", encoding="utf-8")
    masters = tmp_path / "masters.conf"
    masters.write_text(f"master_did = {master.did}\n", encoding="utf-8")
    store = tmp_path / "revocations.jsonl"
    if revoked:
        RevocationStore(store).append(_revocation(master, list(revoked)))
    return ["--identity", str(key), "--authorized", str(allow),
            "--masters", str(masters), "--revocations", str(store)]


def test_cli_node_does_not_start_when_already_revoked(tmp_path, monkeypatch):
    node, coord, master = Identity.generate(), Identity.generate(), Identity.generate()
    calls = []
    monkeypatch.setattr(node_mod, "connect_and_serve", lambda *a, **k: calls.append(1))
    args = _files(tmp_path, node, coord, master, revoked=[node.did])
    # --once bounds the run even if the startup check were missing
    assert cli.main(["node", "--connect", "127.0.0.1:1", "--once", *args]) == 0
    assert calls == []  # never dialed


def test_cli_node_exits_0_without_reconnecting_once_revoked(tmp_path, monkeypatch):
    node, coord, master = Identity.generate(), Identity.generate(), Identity.generate()
    calls = []

    def fake_session(host, port, *, coordinator_allowlist, stop_event, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise KeyboardInterrupt  # it reconnected: end the loop so the test fails, not hangs
        # The revocation reaches this node mid-session.
        coordinator_allowlist.apply(_revocation(master, [node.did]))
        assert stop_event.wait(2)
        return None

    monkeypatch.setattr(node_mod, "connect_and_serve", fake_session)
    args = _files(tmp_path, node, coord, master)
    assert cli.main(["node", "--connect", "127.0.0.1:1", *args]) == 0  # no --once, yet it returns
    assert calls == [1]


@pytest.mark.parametrize("extra", [
    ["--insecure-dev", "--masters", "masters.conf"],           # revocation needs secure mode
])
def test_cli_refuses_revocation_flags_without_secure_mode(extra, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["node", "--connect", "127.0.0.1:1", "--once", *extra])
    assert exc.value.code == 2
    assert "--masters" in capsys.readouterr().err


def test_cli_refuses_revocations_without_masters(tmp_path, capsys):
    node, coord, master = Identity.generate(), Identity.generate(), Identity.generate()
    args = _files(tmp_path, node, coord, master)
    i = args.index("--masters")
    del args[i:i + 2]
    with pytest.raises(SystemExit) as exc:
        cli.main(["node", "--connect", "127.0.0.1:1", "--once", *args])
    assert exc.value.code == 2
    assert "--masters" in capsys.readouterr().err
