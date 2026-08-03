"""Tests for the node's receive/dispatch logic. The task runner is injected, so
none of this needs a desktop, a model, or a network."""
import threading

from secdogie_fleet import protocol as p
from secdogie_fleet.node import ALLOWED_OPTIONS, Node, default_node_id, filter_options


class Wire:
    def __init__(self):
        self.sent = []

    def __call__(self, msg):
        self.sent.append(msg)

    def of(self, cls):
        return [m for m in self.sent if isinstance(m, cls)]


def _node(run_task, **kw):
    wire = Wire()
    return Node("n1", wire, run_task, **kw), wire


def _finished(node, wire):
    node.wait_idle(timeout=5)
    return wire.of(p.Result)


# -- option filtering ----------------------------------------------------------

def test_filter_options_keeps_only_known_agent_flags():
    got = filter_options({"auto": True, "max_steps": 20, "nonsense": 1})
    assert got == {"auto": True, "max_steps": 20}


def test_filter_options_drops_anything_that_could_reach_past_the_allowlist():
    # An assignment must not be able to set arbitrary AgentConfig fields --
    # e.g. point this node at another machine's files or swap its backend.
    hostile = {"backend": object(), "memory_path": "/etc/passwd", "trace_path": "/tmp/x",
               "log_path": "/tmp/y", "should_stop": lambda: True}
    assert filter_options(hostile) == {}
    for key in hostile:
        assert key not in ALLOWED_OPTIONS


def test_default_node_id_is_unique_per_call():
    assert default_node_id() != default_node_id()


# -- hello ---------------------------------------------------------------------

def test_hello_reports_this_desktops_identity():
    node, wire = _node(lambda **kw: (0, ""), label="win11-vm-1",
                       screen=(1920, 1080), capabilities=("desktop-ax",))
    node.hello()
    (hello,) = wire.of(p.Hello)
    assert hello.node_id == "n1" and hello.label == "win11-vm-1"
    assert hello.screen == (1920, 1080) and hello.capabilities == ("desktop-ax",)


# -- running an assignment -----------------------------------------------------

def test_an_assignment_runs_and_reports_a_result():
    seen = {}

    def run_task(task, options, should_stop, on_progress):
        seen["task"], seen["options"] = task, options
        on_progress(3, "clicked Save")
        return 0, "all done"

    node, wire = _node(run_task)
    node.handle(p.Assign(task_id="t1", task="tidy up", options={"auto": True, "junk": 1}))
    results = _finished(node, wire)

    assert seen["task"] == "tidy up"
    assert seen["options"] == {"auto": True}          # filtered on the way in
    (result,) = results
    assert result.task_id == "t1" and result.code == 0 and result.summary == "all done"
    # Progress and a final idle status both reach the coordinator.
    statuses = wire.of(p.Status)
    assert any(s.step == 3 and s.detail == "clicked Save" for s in statuses)
    assert statuses[-1].state == "idle"
    assert node.current_task is None


def test_a_crashing_task_still_reports_a_result():
    # Otherwise the coordinator would wait forever on a node whose task blew up.
    def boom(**kw):
        raise RuntimeError("pyautogui exploded")

    node, wire = _node(boom)
    node.handle(p.Assign(task_id="t1", task="x"))
    (result,) = _finished(node, wire)
    assert result.code == 1 and "pyautogui exploded" in result.summary
    assert node.current_task is None                  # and the node frees itself


def test_a_second_assignment_while_busy_is_handed_back_not_dropped():
    release = threading.Event()

    def slow(task, options, should_stop, on_progress):
        release.wait(5)
        return 0, "done"

    node, wire = _node(slow)
    node.handle(p.Assign(task_id="t1", task="first"))
    node.handle(p.Assign(task_id="t2", task="second"))   # arrives mid-run

    busy = [r for r in wire.of(p.Result) if r.task_id == "t2"]
    assert len(busy) == 1 and busy[0].code == 7          # non-terminal -> coordinator requeues it
    assert "busy" in busy[0].summary

    release.set()
    node.wait_idle(timeout=5)
    assert any(r.task_id == "t1" and r.code == 0 for r in wire.of(p.Result))


# -- stopping ------------------------------------------------------------------

def test_stop_sets_the_flag_the_task_polls():
    started = threading.Event()

    def cooperative(task, options, should_stop, on_progress):
        started.set()
        while not should_stop():
            pass
        return 5, "stopped"

    node, wire = _node(cooperative)
    node.handle(p.Assign(task_id="t1", task="long one"))
    assert started.wait(5)
    node.handle(p.Stop(task_id="t1"))
    (result,) = _finished(node, wire)
    assert result.code == 5


def test_a_stop_for_a_different_task_is_ignored():
    started, release = threading.Event(), threading.Event()

    def cooperative(task, options, should_stop, on_progress):
        started.set()
        release.wait(5)
        return 0, "finished normally"

    node, wire = _node(cooperative)
    node.handle(p.Assign(task_id="t1", task="x"))
    assert started.wait(5)
    node.handle(p.Stop(task_id="t-other"))     # not ours
    assert not node._stop.is_set()
    release.set()
    node.wait_idle(timeout=5)


def test_a_stop_while_idle_is_harmless():
    node, wire = _node(lambda **kw: (0, ""))
    node.handle(p.Stop())                      # must not raise
    assert wire.sent == []
