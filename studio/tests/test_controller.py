"""The window's behaviour without a window: first run on a worker, the node's
states, the link check, and shutdown taking the node with it."""
from __future__ import annotations

from secdogie_studio.controller import EXITED, RUNNING, STOPPED, StudioController
from secdogie_studio.nodeproc import wait_until


def test_first_run_then_node_lifecycle(tmp_path):
    c = StudioController(tmp_path / "p", kdf="min")
    assert c.needs_first_run() and not c.snapshot().initialized
    assert c.create_profile("operator passphrase", roles=("forward", "ingest"))
    c.wait_idle()
    s = c.snapshot()
    assert s.initialized and not s.error and s.node_state == STOPPED
    assert s.roles == ("forward", "ingest")
    assert len({s.node_did, s.app_did, s.operator_did}) == 3

    c.start_node()
    assert wait_until(lambda: c.snapshot().node_state == RUNNING, 30), c.snapshot()
    s = c.snapshot()
    assert s.listen.startswith("127.0.0.1:") and s.pid
    assert c.check_link()
    c.wait_idle()
    assert c.snapshot().link.startswith("refused: no active memory"), c.snapshot()

    assert c.shutdown() == 0
    s = c.snapshot()
    assert s.node_state == EXITED and s.returncode == 0


def test_a_short_passphrase_is_reported_not_raised(tmp_path):
    c = StudioController(tmp_path / "p", kdf="min")
    c.create_profile("short")
    c.wait_idle()
    s = c.snapshot()
    assert not s.initialized and "at least" in s.error


def test_link_check_needs_a_running_node(profile):
    c = StudioController(profile.root, kdf="min")
    assert not c.check_link()
    assert "not running" in c.snapshot().error


def test_one_worker_at_a_time(tmp_path):
    import threading

    c = StudioController(tmp_path / "p", kdf="min")
    release = threading.Event()
    assert c._run("holding", release.wait)  # a worker that stays busy until released
    try:
        assert c.snapshot().busy == "holding"
        assert not c.create_profile("operator passphrase")  # refused while the first runs
        assert not c.profile.initialized
    finally:
        release.set()
        c.wait_idle()
    assert c.snapshot().busy == ""
    assert c.create_profile("operator passphrase")
    c.wait_idle()
    assert c.profile.initialized
