"""The node is Studio's child: it announces itself, stops cleanly when asked,
and -- the property the window relies on -- stops by itself when its parent
dies, however the parent dies."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from secdogie_studio.nodeproc import NodeError, NodeProcess, node_command, wait_until


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():  # a zombie waiting for a non-reaping init is not running
        try:
            return stat.read_text().split(")")[-1].split()[0] != "Z"
        except OSError:
            return False
    return True


def test_the_node_announces_its_did_and_stops_cleanly(profile):
    with NodeProcess(profile.node_args()) as node:
        ready = node.wait_ready(30)
        assert ready.did == profile.node_did()
        assert ready.host == "127.0.0.1" and ready.port > 0
        assert node.running
    assert node.returncode == 0 and not node.running
    assert any("standard input closed" in line for line in node.log_tail(50))


def test_a_node_that_cannot_start_says_why(profile):
    profile.node_key.write_text("not a key", encoding="utf-8")
    node = NodeProcess(profile.node_args())
    node.start()
    try:
        with pytest.raises(NodeError, match="exited with code"):
            node.wait_ready(30)
    finally:
        node.stop()


def test_stop_before_start_and_twice_is_harmless(profile):
    node = NodeProcess(profile.node_args())
    assert node.stop() is None
    node.start()
    node.wait_ready(30)
    assert node.stop() == 0
    assert node.stop() == 0
    with pytest.raises(NodeError):
        node.start()


def test_the_command_runs_the_installed_node():
    assert node_command(["run"])[1:] == ["-m", "secdogie_node.cli", "run"]


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL of the parent; the pipe rule is the same on Windows")
def test_the_node_does_not_outlive_a_killed_parent(profile):
    parent = textwrap.dedent(f"""
        import sys, time
        from secdogie_studio.nodeproc import NodeProcess
        from secdogie_studio.profile import Profile
        node = NodeProcess(Profile({str(profile.root)!r}).node_args())
        node.start()
        node.wait_ready(30)
        print(node.pid, flush=True)
        time.sleep(600)
    """)
    proc = subprocess.Popen([sys.executable, "-c", parent], stdout=subprocess.PIPE, text=True)
    try:
        child = int(proc.stdout.readline())
        assert _alive(child)
        proc.send_signal(signal.SIGKILL)  # no cleanup code runs in the parent
        proc.wait(timeout=10)
        assert wait_until(lambda: not _alive(child), 20), "the node outlived its parent"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
