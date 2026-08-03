"""Tests for the fleet scheduling brain. No sockets, no VMs: `send` is a list
and the clock is injected, so the whole lifecycle -- register, dispatch,
progress, finish, drop, requeue, cap -- is provable headless."""
from secdogie_fleet import protocol as p
from secdogie_fleet.coordinator import DONE, FAILED, QUEUED, RUNNING, Coordinator


class Wire:
    """Records everything the coordinator sends, as (node_id, message)."""

    def __init__(self):
        self.sent = []

    def __call__(self, node_id, msg):
        self.sent.append((node_id, msg))

    def assigns(self):
        return [(n, m) for n, m in self.sent if isinstance(m, p.Assign)]

    def stops(self):
        return [(n, m) for n, m in self.sent if isinstance(m, p.Stop)]


def _coord(**kw):
    wire = Wire()
    return Coordinator(wire, **kw), wire


def _join(coord, *node_ids):
    for nid in node_ids:
        coord.on_hello(p.Hello(node_id=nid, label=f"vm-{nid}", screen=(1920, 1080)))


# -- registration + dispatch ---------------------------------------------------

def test_a_task_is_dispatched_to_an_idle_node():
    coord, wire = _coord()
    _join(coord, "n1")
    t = coord.submit("open notepad", {"auto": True})

    assert coord.pump() == 1
    assert t.state == RUNNING and t.node_id == "n1" and t.attempts == 1
    (node_id, msg), = wire.assigns()
    assert node_id == "n1"
    assert msg.task == "open notepad" and msg.options == {"auto": True}


def test_tasks_queue_until_a_node_shows_up():
    coord, wire = _coord()
    coord.submit("first")
    assert coord.pump() == 0          # nowhere to run it yet
    assert coord.queued_count == 1

    _join(coord, "n1")
    assert coord.pump() == 1
    assert wire.assigns()[0][0] == "n1"


def test_each_node_holds_at_most_one_task_and_the_rest_wait():
    coord, _ = _coord()
    _join(coord, "n1", "n2")
    for i in range(4):
        coord.submit(f"task {i}")

    assert coord.pump() == 2          # two desktops, two tasks in flight
    assert coord.running_count == 2
    assert coord.queued_count == 2
    assert all(n.busy for n in coord.nodes.values())


def test_max_concurrent_caps_the_fleet_below_the_node_count():
    # The binding limit is usually the shared API quota, not the desktops.
    coord, _ = _coord(max_concurrent=1)
    _join(coord, "n1", "n2", "n3")
    for i in range(3):
        coord.submit(f"task {i}")

    assert coord.pump() == 1
    assert coord.running_count == 1
    assert len(coord.idle_nodes()) == 2   # nodes free, but the cap holds


# -- progress + completion -----------------------------------------------------

def test_status_updates_are_recorded_on_the_task():
    coord, _ = _coord()
    _join(coord, "n1")
    t = coord.submit("do it")
    coord.pump()

    coord.on_status(p.Status(node_id="n1", state="running", task_id=t.task_id, step=7, detail="clicked Save"))
    assert t.step == 7 and t.detail == "clicked Save"


def test_status_from_an_unknown_node_is_ignored_not_an_error():
    coord, _ = _coord()
    coord.on_status(p.Status(node_id="ghost", state="running"))  # must not raise


def test_a_finished_task_frees_its_node_for_the_next_one():
    coord, wire = _coord()
    _join(coord, "n1")
    t1 = coord.submit("first")
    t2 = coord.submit("second")
    coord.pump()
    assert t2.state == QUEUED

    coord.on_result(p.Result(node_id="n1", task_id=t1.task_id, code=0, summary="ok"))
    assert t1.state == DONE and t1.summary == "ok"
    assert not coord.nodes["n1"].busy

    assert coord.pump() == 1           # the freed node picks up the next task
    assert t2.state == RUNNING
    assert [m.task for _, m in wire.assigns()] == ["first", "second"]


def test_terminal_exit_codes_finish_rather_than_retry():
    # 0 done, 2 user declined, 3 steps exhausted, 5 stopped -- the task is over,
    # not broken, so it must not bounce to another node.
    for code in (0, 2, 3, 5):
        coord, wire = _coord()
        _join(coord, "n1")
        t = coord.submit("x")
        coord.pump()
        coord.on_result(p.Result(node_id="n1", task_id=t.task_id, code=code))
        assert t.state == DONE, code
        assert coord.pump() == 0
        assert len(wire.assigns()) == 1, code


def test_an_infrastructure_failure_is_retried_on_another_node():
    coord, wire = _coord(max_attempts=2)
    _join(coord, "n1", "n2")
    t = coord.submit("x")
    coord.pump()                       # -> n1
    assert t.node_id == "n1"

    # Exit code 4 = no display on that node; another desktop may well work.
    coord.on_result(p.Result(node_id="n1", task_id=t.task_id, code=4))
    assert t.state == QUEUED
    coord.pump()
    assert t.state == RUNNING and t.node_id == "n2" and t.attempts == 2
    assert [n for n, _ in wire.assigns()] == ["n1", "n2"]


def test_a_retry_prefers_a_desktop_that_hasnt_tried_the_task():
    # n1 fails and is immediately idle again; the retry should still go to n2,
    # since a node-side failure would just repeat on the same box.
    coord, _ = _coord(max_attempts=3)
    _join(coord, "n1", "n2")
    t = coord.submit("x")
    coord.pump()
    assert t.node_id == "n1"
    coord.on_result(p.Result(node_id="n1", task_id=t.task_id, code=4))
    coord.pump()
    assert t.node_id == "n2"


def test_a_single_node_fleet_still_retries_on_that_node():
    # With nowhere else to go, retrying where it failed beats not retrying.
    coord, _ = _coord(max_attempts=2)
    _join(coord, "n1")
    t = coord.submit("x")
    coord.pump()
    coord.on_result(p.Result(node_id="n1", task_id=t.task_id, code=4))
    coord.pump()
    assert t.state == RUNNING and t.node_id == "n1" and t.attempts == 2


def test_a_task_that_keeps_failing_is_failed_not_bounced_forever():
    coord, _ = _coord(max_attempts=2)
    _join(coord, "n1", "n2")
    t = coord.submit("x")
    coord.pump()
    coord.on_result(p.Result(node_id="n1", task_id=t.task_id, code=4))
    coord.pump()
    coord.on_result(p.Result(node_id="n2", task_id=t.task_id, code=4))

    assert t.state == FAILED
    assert coord.pump() == 0
    assert coord.is_settled()


# -- node loss -----------------------------------------------------------------

def test_a_lost_node_hands_its_task_back_to_the_fleet():
    coord, wire = _coord()
    _join(coord, "n1", "n2")
    t = coord.submit("x")
    coord.pump()                       # -> n1
    assert t.node_id == "n1"

    coord.on_node_lost("n1")           # the VM died / network dropped
    assert "n1" not in coord.nodes
    assert t.state == QUEUED

    coord.pump()
    assert t.state == RUNNING and t.node_id == "n2"


def test_losing_an_idle_node_disturbs_nothing():
    coord, _ = _coord()
    _join(coord, "n1", "n2")
    t = coord.submit("x")
    coord.pump()                       # -> n1
    coord.on_node_lost("n2")
    assert t.state == RUNNING and t.node_id == "n1"


def test_a_reconnecting_node_releases_the_task_it_lost():
    # Its agent process restarted, so whatever it held is gone.
    coord, _ = _coord()
    _join(coord, "n1")
    t = coord.submit("x")
    coord.pump()

    coord.on_hello(p.Hello(node_id="n1"))   # same id dials in again
    assert t.state == QUEUED
    assert not coord.nodes["n1"].busy


def test_a_late_result_from_a_dropped_node_is_harmless():
    coord, _ = _coord()
    _join(coord, "n1", "n2")
    t = coord.submit("x")
    coord.pump()
    coord.on_node_lost("n1")
    coord.pump()                       # requeued onto n2

    # n1's result finally arrives; it must not clobber the run now on n2.
    coord.on_result(p.Result(node_id="n1", task_id=t.task_id, code=0))
    assert t.state == DONE              # first terminal answer wins
    coord.on_result(p.Result(node_id="n2", task_id=t.task_id, code=1))
    assert t.state == DONE              # and a later one can't undo it


# -- stop + settle -------------------------------------------------------------

def test_stop_asks_the_holding_node_and_waits_for_its_answer():
    coord, wire = _coord()
    _join(coord, "n1")
    t = coord.submit("x")
    coord.pump()

    assert coord.stop_task(t.task_id) is True
    (node_id, msg), = wire.stops()
    assert node_id == "n1" and msg.task_id == t.task_id
    assert t.state == RUNNING          # still running until the node confirms

    coord.on_result(p.Result(node_id="n1", task_id=t.task_id, code=5))
    assert t.state == DONE


def test_stopping_something_not_running_is_a_no_op():
    coord, wire = _coord()
    _join(coord, "n1")
    t = coord.submit("x")              # queued, not dispatched
    assert coord.stop_task(t.task_id) is False
    assert coord.stop_task("nope") is False
    assert wire.stops() == []


def test_is_settled_tracks_outstanding_work():
    coord, _ = _coord()
    _join(coord, "n1")
    assert coord.is_settled()
    t = coord.submit("x")
    assert not coord.is_settled()      # queued
    coord.pump()
    assert not coord.is_settled()      # running
    coord.on_result(p.Result(node_id="n1", task_id=t.task_id, code=0))
    assert coord.is_settled()


def test_duplicate_task_ids_are_rejected():
    coord, _ = _coord()
    coord.submit("x", task_id="same")
    try:
        coord.submit("y", task_id="same")
    except ValueError as e:
        assert "already exists" in str(e)
    else:
        raise AssertionError("expected a ValueError for a duplicate task id")
