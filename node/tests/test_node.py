"""The node's own logic, without a full run: zero-trust construction, control
requests, one App at a time, operator hooks that follow the App's liveness,
and memory offers."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.consolidate import create_confirmation  # noqa: E402
from secdogie_citadel.lessons import MemoryClass  # noqa: E402
from secdogie_dialogue.protocol import ControlOp, ControlPacket, MemoryCandidatePacket  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402
from secdogie_node import Node, NodeConfig  # noqa: E402

NODE, APP, OTHER_APP, OPERATOR, STRANGER = (Identity.generate() for _ in range(5))


def make(**kw):
    cfg = dict(identity=NODE, apps=Allowlist({APP.did, OTHER_APP.did}), operators=Allowlist({OPERATOR.did}),
               authorized=Allowlist({NODE.did}), run_task=lambda *a, **k: (0, "ok"), idle_poll=0.05)
    cfg.update(kw)
    return Node(NodeConfig(**cfg))


@pytest.fixture
def node():
    n = make()
    yield n
    n.stop()


@pytest.mark.parametrize("missing", ["apps", "operators", "authorized"])
def test_a_node_needs_every_trust_set(missing):
    with pytest.raises(ValueError, match="allowlist"):
        make(**{missing: None})


def test_without_issuers_every_mutating_action_is_refused():
    seen = {}

    def task(t, *, should_stop, on_status, confirm, record_step=None, plan_gate=None, **_):
        seen["click"] = plan_gate({"kind": "left_click", "x": 1, "y": 1}, [])
        return 0, "ok"

    n = make(run_task=task)
    try:
        n.supervisor.add_goal("g1", title="g1")
        assert n.run_ready_once() is True
        assert seen["click"][0] is False
    finally:
        n.stop()


def ctl(op, **kw):
    return ControlPacket("r1", op, **kw)


def test_goal_controls(node):
    assert node.on_control(ctl(ControlOp.ADD_GOAL, goal_id="g1", title="tidy"), APP.did).startswith("accepted")
    assert node.supervisor.pending_ready() == ["g1"]
    assert node.on_control(ctl(ControlOp.PAUSE, goal_id="g1"), APP.did).startswith("accepted")
    assert node.supervisor.pending_ready() == []
    assert node.on_control(ctl(ControlOp.RESUME, goal_id="g1"), APP.did).startswith("accepted")
    assert node.supervisor.pending_ready() == ["g1"]
    assert node.on_control(ctl(ControlOp.STOP, goal_id="g1"), APP.did).startswith("accepted")


def test_memory_confirmation_and_retraction(node):
    c = node.supervisor._candidates.note("reports go to ~/Reports", key="report-folder")
    forged = create_confirmation(STRANGER, c.candidate_id, NODE.did)  # not an App key
    reply = node.on_control(ctl(ControlOp.CONFIRM_MEMORY, memory_id=c.candidate_id, confirmation=forged), APP.did)
    assert reply.startswith("refused") and node.supervisor.memory_view().records == {}
    wrong_node = create_confirmation(APP, c.candidate_id, OTHER_APP.did)
    assert node.on_control(ctl(ControlOp.CONFIRM_MEMORY, memory_id=c.candidate_id, confirmation=wrong_node),
                           APP.did).startswith("refused")
    good = create_confirmation(APP, c.candidate_id, NODE.did)
    assert node.on_control(ctl(ControlOp.CONFIRM_MEMORY, memory_id=c.candidate_id, confirmation=good),
                           APP.did).startswith("accepted")
    assert c.candidate_id in node.supervisor.memory_view().records
    assert node.on_control(ctl(ControlOp.RETRACT_MEMORY, memory_id="nope"), APP.did).startswith("refused")
    assert node.on_control(ctl(ControlOp.RETRACT_MEMORY, memory_id=c.candidate_id), APP.did).startswith("accepted")
    assert node.supervisor.memory_view().records == {}


def test_one_app_at_a_time_and_only_apps_on_the_list(node):
    assert node._accept(STRANGER.did) is None
    first = node._accept(APP.did)
    assert first is not None
    assert node._accept(OTHER_APP.did) is None  # busy: the first App is alive
    first._alive = False  # the first App went away
    second = node._accept(OTHER_APP.did)
    assert second is not None and node._link.session is second


def test_operator_hooks_follow_the_apps_liveness(node):
    session = node._accept(APP.did)
    assert node.supervisor._hooks.ask is not None and node.supervisor._hooks.authorize is not None
    session.on_peer_down()
    assert node.supervisor._hooks.ask is None and node.supervisor._hooks.authorize is None
    assert node.supervisor._confirm("g", "delete?", True) is False  # no App: a no, at once
    session.on_peer_up()
    assert node.supervisor._hooks.ask is not None


def test_no_app_means_no_operator(node):
    assert node.supervisor._hooks.ask is None
    assert node.supervisor._confirm("g", "delete?", True) is False


def test_facts_and_preferences_are_offered_once_cautions_never(node):
    session = node._accept(APP.did)
    sent = []
    session.send = lambda pkt, **kw: sent.append(pkt)
    store = node.supervisor._candidates
    fact = store.note("reports go to ~/Reports", key="report-folder")
    pref = store.note("dark theme", key="theme", mclass=MemoryClass.PREFERENCE)
    store.note("avoid clicking at 5,5", key="k" * 64, mclass=MemoryClass.CAUTION, source="consolidation")
    node._offer_memories()
    node._offer_memories()
    offered = [p for p in sent if isinstance(p, MemoryCandidatePacket)]
    assert sorted(p.memory_id for p in offered) == sorted([fact.candidate_id, pref.candidate_id])
    assert all(p.mclass in ("fact", "preference") for p in offered)


def test_after_stop_no_app_is_accepted():
    n = make()
    n.start()
    n.stop()
    assert n._accept(APP.did) is None


def test_each_goal_is_reported_and_its_notes_offered(node):
    def task(t, *, should_stop, on_status, confirm, record_step=None, remember=None, **_):
        remember("reports go to ~/Reports", "report-folder")
        return 0, "done"

    node.supervisor.run_task = task
    session = node._accept(APP.did)
    sent = []
    session.send = lambda pkt, **kw: sent.append(pkt)
    node.supervisor.add_goal("g1", title="file the report")
    assert node.run_ready_once() is True
    statuses = [p.content for p in sent if getattr(p, "content", "").startswith("goal ")]
    assert statuses == ["goal g1 finished: exit 0 -- done"]
    (offer,) = [p for p in sent if isinstance(p, MemoryCandidatePacket)]
    assert offer.key == "report-folder"
    assert node.run_ready_once() is False  # nothing left


def test_a_stopped_node_runs_nothing_more():
    n = make()
    n.stop()
    n.supervisor.add_goal("g1", title="g1")
    assert n.run_ready_once() is False


def test_stop_interrupts_the_running_goal():
    import threading
    import time

    running, results = threading.Event(), []

    def task(t, *, should_stop, on_status, confirm, record_step=None, **_):
        running.set()
        while not should_stop():
            time.sleep(0.01)
        results.append("stopped")
        return 5, "stopped"

    n = make(run_task=task)
    n.start()
    n.supervisor.add_goal("g1", title="g1")
    n._wake.set()
    assert running.wait(5)
    t0 = time.monotonic()
    n.stop(timeout=5)
    assert time.monotonic() - t0 < 3 and results == ["stopped"]
