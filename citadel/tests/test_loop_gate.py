"""The live-loop bridge for the action-plan gate (M3 wiring).

Inside the loop only authorization findings block; the gate's repeat/polling
heuristics are advisory, because the loop can't afford to refuse a second
scroll or a run of waits (it has its own screen-change-aware stall check).
"""
from __future__ import annotations

from secdogie_citadel.loop_gate import make_plan_gate, to_planned


def _v(kind, **kw):
    view = {"kind": kind, "element": None, "x": None, "y": None, "text": "", "keys": [],
            "path": "", "high_risk": False}
    view.update(kw)
    return view


def test_agent_kinds_map_to_gate_kinds():
    click = to_planned(_v("left_click", x=10, y=20))
    assert click.kind == "click" and click.target_id == "xy:10,20"
    assert to_planned(_v("click_element", element="e3")).target_id == "element:e3"
    chord = to_planned(_v("hold_key", keys=["ctrl", "s"]))
    assert chord.kind == "key" and chord.text == "ctrl+s"
    assert to_planned(_v("teleport")).kind == "teleport"  # unknown passes through


def test_granted_click_allowed_ungranted_type_refused():
    g = make_plan_gate({"physical.click"})
    assert g(_v("left_click", x=1, y=1), [])[0]
    ok, reason = g(_v("type", text="hi"), [])
    assert not ok and "physical.type" in reason


def test_elevated_launch_is_refused():
    g = make_plan_gate({"process.run", "physical.click"})
    assert not g(_v("run_elevated", path="setup.exe", high_risk=True), [])[0]


def test_unknown_mutating_kind_is_refused():
    assert not make_plan_gate({"physical.click"})(_v("teleport"), [])[0]


def test_repeat_and_polling_heuristics_do_not_block():
    g = make_plan_gate({"physical.scroll"})
    scroll = _v("scroll", x=5, y=5)
    ok, note = g(scroll, [scroll, scroll])  # no-op + repeated findings
    assert ok and note                      # allowed, but noted for the log
    wait = _v("wait")
    assert g(wait, [wait, wait])[0]          # busy-poll finding is advisory too


def test_not_enforced_allows_everything():
    assert make_plan_gate(set(), enforce=False)(_v("type", text="x"), [])[0]
