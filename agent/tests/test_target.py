"""Headless tests for AX opaque target / generation gate (Phase 2.5).

The pure re-resolution logic and both wired seams (atlas.run_hybrid_step's
generation gate + uniqueness guard) are checked on Linux with no desktop."""
from __future__ import annotations

from secdogie_agent import atlas, target
from secdogie_agent.atlas import ControlNode, LoopAction, LoopConfig, Rect, Selector
from secdogie_agent.axtree import AxElement

# --- gen_gate ---------------------------------------------------------------


def test_gen_gate_stale_on_any_mismatch():
    assert target.gen_gate(3, 5) == target.STALE  # older -> stale
    assert target.gen_gate(5, 5) is None  # current -> ok
    assert target.gen_gate(6, 5) == target.STALE  # a ref from the "future" is also refused


# --- unique_identity_match --------------------------------------------------


def test_unique_identity_match_unique_zero_ambiguous_and_empty():
    a = AxElement("Button", "Save", "save", (0, 0, 10, 10))
    b = AxElement("Button", "Cancel", "cancel", (20, 0, 30, 10))
    verdict, el = target.unique_identity_match([a, b], automation_id="save")
    assert verdict == target.VALID and el is a
    assert target.unique_identity_match([a, b], automation_id="nope")[0] == target.GONE
    dup1 = AxElement("Button", "OK", "", (0, 0, 10, 10))
    dup2 = AxElement("Button", "OK", "", (40, 0, 50, 10))
    assert target.unique_identity_match([dup1, dup2], name="OK", role="Button")[0] == target.AMBIGUOUS
    # empty selector matches nothing (never latches onto the first element)
    assert target.unique_identity_match([a, b])[0] == target.GONE


class _Node:
    """An atlas.ControlNode-shaped duck to prove the identity core is not tied
    to AxElement (it must also serve the atlas ControlNode path)."""

    def __init__(self, role, name, automation_id):
        self.role, self.name, self.automation_id = role, name, automation_id


def test_unique_identity_match_is_duck_typed():
    n = _Node("Button", "Zoom", "z")
    verdict, el = target.unique_identity_match([n], automation_id="Z")  # case-insensitive
    assert verdict == target.VALID and el is n


# --- AXTargetRef ------------------------------------------------------------


def test_axtargetref_from_element_and_center():
    el = AxElement("Button", "Save", "save", (0, 0, 20, 10))
    ref = target.AXTargetRef.from_element(el, window_id=1, app_pid=9, generation=4)
    assert ref.role == "Button" and ref.automation_id == "save"
    assert ref.bounds == (0, 0, 20, 10) and ref.center == (10, 5)


# --- resolve ----------------------------------------------------------------


def test_resolve_stale_does_not_even_search():
    el = AxElement("Button", "Save", "save", (0, 0, 20, 10))
    ref = target.AXTargetRef.from_element(el, window_id=1, app_pid=1, generation=2)
    # The snapshot DOES contain a perfect match, but the generation advanced, so
    # the ref is stale and must not resolve -- re-observe instead of re-search.
    res = target.resolve(ref, [el], current_generation=3)
    assert res.verdict == target.STALE and res.element is None


def test_resolve_unique_is_valid():
    el = AxElement("Button", "Save", "save", (0, 0, 20, 10))
    ref = target.AXTargetRef.from_element(el, window_id=1, app_pid=1, generation=5)
    res = target.resolve(ref, [el], current_generation=5)
    assert res.ok and res.element is el


def test_resolve_ambiguous_is_refused():
    dup1 = AxElement("Button", "OK", "", (0, 0, 10, 10))
    dup2 = AxElement("Button", "OK", "", (40, 0, 50, 10))
    ref = target.AXTargetRef(window_id=1, app_pid=1, generation=1, role="Button", name="OK")
    res = target.resolve(ref, [dup1, dup2], current_generation=1)
    assert res.verdict == target.AMBIGUOUS
    el, _ = target.resolve_or_reject(ref, [dup1, dup2], current_generation=1)
    assert el is None  # never guesses one of the lookalikes


def test_native_identity_disambiguates_lookalikes():
    dup1 = AxElement("Button", "OK", "", (0, 0, 10, 10))
    dup2 = AxElement("Button", "OK", "", (40, 0, 50, 10))
    tokens = {id(dup1): "rt-1", id(dup2): "rt-2"}
    ref = target.AXTargetRef(
        window_id=1, app_pid=1, generation=1, role="Button", name="OK", native_identity="rt-2"
    )
    res = target.resolve(
        ref, [dup1, dup2], current_generation=1, identity_of=lambda e: tokens[id(e)]
    )
    assert res.ok and res.element is dup2


def test_resolve_moved_when_bounds_shift():
    ref = target.AXTargetRef(
        window_id=1, app_pid=1, generation=1, automation_id="save", bounds=(0, 0, 20, 10)
    )
    moved = AxElement("Button", "Save", "save", (500, 500, 520, 510))
    res = target.resolve(ref, [moved], current_generation=1)
    assert res.verdict == target.MOVED
    el, _ = target.resolve_or_reject(ref, [moved], current_generation=1)
    assert el is None


def test_resolve_gone_when_absent():
    ref = target.AXTargetRef(window_id=1, app_pid=1, generation=1, automation_id="save")
    assert target.resolve(ref, [], current_generation=1).verdict == target.GONE


# --- identity keys (the 2.5 <-> 2.6 connective tissue) ----------------------


def test_identity_key_prefers_automation_id_then_role_name():
    assert target.identity_key(AxElement("Button", "Save", "save", (0, 0, 1, 1))) == "id=save"
    assert target.identity_key(AxElement("Button", "OK", "", (0, 0, 1, 1))) == "button:ok"
    # case-normalized so presence checks are case-insensitive (matches matching)
    assert target.identity_key(AxElement("BUTTON", "Ok", "", (0, 0, 1, 1))) == "button:ok"
    # anonymous control -> no key
    assert target.identity_key(AxElement("", "", "", (0, 0, 1, 1))) == ""


def test_present_keys_drops_anonymous_and_dedupes():
    els = [
        AxElement("Button", "Save", "save", (0, 0, 1, 1)),
        AxElement("", "", "", (0, 0, 1, 1)),  # anonymous -> dropped
        AxElement("Button", "OK", "", (0, 0, 1, 1)),
    ]
    assert target.present_keys(els) == frozenset({"id=save", "button:ok"})


def test_axtargetref_key_matches_module_function():
    el = AxElement("Button", "Save", "save", (0, 0, 20, 10))
    ref = target.AXTargetRef.from_element(el, window_id=1, app_pid=1, generation=1)
    assert ref.key == target.identity_key(el) == "id=save"


# --- atlas.run_hybrid_step integration --------------------------------------


def _zoom_node():
    return ControlNode(
        id="z", role="Button", name="Zoom", automation_id="ID_Z", bounds=Rect(10, 10, 80, 24)
    )


def test_run_hybrid_step_stale_generation_blocks_execution():
    calls = []
    step = atlas.run_hybrid_step(
        LoopAction("z", selector=Selector(automation_id="ID_Z")),
        snapshot=lambda: [_zoom_node()],
        capture=lambda _r: b"",
        execute=lambda _n, a: calls.append(a.id),
        observed_generation=1,
        current_generation=2,
    )
    assert step.status == atlas.STALE
    assert calls == []  # a stale target is never executed


def test_run_hybrid_step_ambiguous_selector_does_not_click_the_first():
    dup1 = ControlNode(id="a", role="Button", name="OK", automation_id="", bounds=Rect(0, 0, 10, 10))
    dup2 = ControlNode(id="b", role="Button", name="OK", automation_id="", bounds=Rect(40, 0, 10, 10))
    calls = []
    step = atlas.run_hybrid_step(
        LoopAction("ok", selector=Selector(name="OK", role="Button")),
        snapshot=lambda: [dup1, dup2],
        capture=lambda _r: b"",
        execute=lambda _n, a: calls.append(a.id),
        config=LoopConfig(vision_fallback=False),  # ambiguity -> FAILED, deterministic
    )
    assert step.status == atlas.FAILED
    assert calls == []  # refused, not resolved to dup1


def test_run_hybrid_step_same_generation_executes_normally():
    state = {"n": 0}

    def cap(_r):
        state["n"] += 1
        return bytes([10, 10, 10, 255]) * 16 if state["n"] == 1 else bytes([200, 200, 200, 255]) * 16

    step = atlas.run_hybrid_step(
        LoopAction("z", selector=Selector(automation_id="ID_Z")),
        snapshot=lambda: [_zoom_node()],
        capture=cap,
        execute=lambda _n, _a: None,
        observed_generation=7,
        current_generation=7,  # equal -> proceeds exactly as before
    )
    assert step.status == atlas.PASSED
