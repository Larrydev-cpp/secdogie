"""Tests for the Atlas decision core (atlas.py).

The Win32 OpenProcess / UIA COM / GDI capture half is on-machine (native/atlas
and desktop_ax.py). What's proved here is the safety wall and the hybrid loop:
read-only handle enforcement, TrustedInstaller / anti-EDR refusal, allowlist
exact-match, UIA-first targeting with vision fallback, high-risk gate, and
pixel-diff verification that never records a no-mutation success.
"""
from secdogie_agent import atlas as a
from secdogie_agent.atlas import ControlNode, LoopAction, LoopConfig, Rect, Selector


def test_query_plus_vm_read_is_granted():
    d = a.enforce_read_only(a.PROCESS_QUERY_INFORMATION | a.PROCESS_VM_READ)
    assert d.refused is False
    assert d.granted == (a.PROCESS_QUERY_INFORMATION | a.PROCESS_VM_READ)
    assert d.code == a.OK


def test_vm_write_is_refused_not_narrowed():
    d = a.enforce_read_only(a.PROCESS_VM_WRITE)
    assert d.refused is True
    assert d.granted == 0
    assert d.code == a.DENIED_WRITE


def test_all_access_is_refused_not_narrowed():
    d = a.enforce_read_only(a.PROCESS_ALL_ACCESS)
    assert d.refused is True
    assert d.granted == 0
    assert d.code == a.DENIED_WRITE


def test_empty_desired_is_denied():
    d = a.enforce_read_only(0)
    assert d.refused is True
    assert d.code == a.DENIED_EMPTY


def test_trusted_installer_impersonation_is_refused():
    r = a.try_impersonate_trusted_installer()
    assert r.ok is False
    assert r.code == a.REFUSED_IDENTITY
    assert "TrustedInstaller" in r.detail
    assert "never steals" in r.detail


def test_anti_edr_is_refused():
    r = a.try_edr_evasion()
    assert r.ok is False
    assert r.code == a.REFUSED_IDENTITY
    assert "Anti-EDR" in r.detail


def test_empty_allowlist_permits_nothing():
    assert a.is_allowlisted("sc stop Spooler", ()) is False
    assert a.is_allowlisted("anything", []) is False


def test_allowlist_is_exact_not_prefix():
    allow = ("sc stop Spooler",)
    assert a.is_allowlisted("sc stop Spooler", allow) is True
    assert a.is_allowlisted("  sc   stop\tSpooler", allow) is True
    assert a.is_allowlisted("sc stop Spooler && calc", allow) is False
    assert a.is_allowlisted("sc stop Themes", allow) is False


def test_plan_system_launch_order_and_allowlist():
    # OS first, then privilege, then empty command, then allowlist, then session.
    r = a.plan_system_launch("cmd", elevated=True, session_id=1, on_windows=False, allowlist=("cmd",))
    assert r.code == a.UNSUPPORTED
    r = a.plan_system_launch("cmd", elevated=False, session_id=1, on_windows=True, allowlist=("cmd",))
    assert r.code == a.NOT_ELEVATED
    r = a.plan_system_launch("  ", elevated=True, session_id=1, on_windows=True, allowlist=("cmd",))
    assert r.code == a.BAD_COMMAND
    r = a.plan_system_launch("cmd", elevated=True, session_id=1, on_windows=True, allowlist=())
    assert r.code == a.NOT_ALLOWLISTED
    r = a.plan_system_launch("cmd", elevated=True, session_id=None, on_windows=True, allowlist=("cmd",))
    assert r.code == a.NO_SESSION
    r = a.plan_system_launch("cmd", elevated=True, session_id=2, on_windows=True, allowlist=("cmd",))
    assert r.ok is True and r.code == a.OK


def test_find_control_by_automation_id():
    btn = ControlNode(
        role="Button", name="Zoom Extents", automation_id="ID_ZOOM_EXTENTS",
        bounds=Rect(10, 10, 80, 24),
    )
    hit = a.find_control([btn], Selector(automation_id="ID_ZOOM_EXTENTS"))
    assert hit is btn
    assert a.find_control([btn], Selector(automation_id="NOPE")) is None
    assert a.find_control([btn], Selector()) is None  # empty selector matches nothing


def test_pixel_diff_identical_and_mutated():
    same = bytes([40, 40, 40, 255]) * (16 * 16)
    other = bytes([200, 200, 200, 255]) * (16 * 16)
    assert a.changed_ratio(same, same) < 0.001
    assert a.changed_ratio(same, other) > a.DIFF_THRESHOLD
    assert a.djb2(same) == a.djb2(same)
    assert a.djb2(same) != a.djb2(other)


def test_djb2_hashes_every_byte_not_just_one_channel():
    # A regression: hashing only B (every 4th byte) would collide these.
    a_bytes = bytes([1, 2, 3, 255, 4, 5, 6, 255])
    b_bytes = bytes([1, 9, 3, 255, 4, 9, 6, 255])  # G channel differs
    assert a.djb2(a_bytes) != a.djb2(b_bytes)


def _tree(uia_ok: bool):
    btn = ControlNode(
        id="zoom", role="Button", name="Zoom Extents",
        automation_id="ID_ZOOM_EXTENTS", bounds=Rect(10, 10, 80, 24),
    )
    return [btn] if uia_ok else []


def test_hybrid_loop_uia_then_pixel_diff_pass():
    frames = {
        "before": bytes([10, 10, 10, 255]) * 16,
        "after": bytes([200, 200, 200, 255]) * 16,
    }
    state = {"n": 0}

    def capture(_rect):
        state["n"] += 1
        return frames["before"] if state["n"] == 1 else frames["after"]

    executed = []
    step = a.run_hybrid_step(
        LoopAction("zoom", selector=Selector(automation_id="ID_ZOOM_EXTENTS")),
        snapshot=lambda: _tree(True),
        capture=capture,
        execute=lambda node, act: executed.append(act.id),
    )
    assert step.status == a.PASSED
    assert step.mode == "uia"
    assert executed == ["zoom"]
    assert step.diff_ratio > a.DIFF_THRESHOLD


def test_hybrid_loop_no_mutation_is_failed_not_passed():
    flat = bytes([40, 40, 40, 255]) * 16
    step = a.run_hybrid_step(
        LoopAction("zoom", selector=Selector(automation_id="ID_ZOOM_EXTENTS")),
        snapshot=lambda: _tree(True),
        capture=lambda _r: flat,
        execute=lambda node, act: None,
        config=LoopConfig(max_retries=1),
    )
    assert step.status == a.FAILED
    assert "No visible mutation" in step.detail


def test_hybrid_loop_blocks_high_risk_without_operator():
    step = a.run_hybrid_step(
        LoopAction("save", high_risk=True, selector=Selector(name="Save")),
        snapshot=lambda: _tree(True),
        capture=lambda _r: b"",
        execute=lambda n, act: (_ for _ in ()).throw(AssertionError("must not execute")),
    )
    assert step.status == a.BLOCKED
    assert "Operator confirmation" in step.detail


def test_hybrid_loop_vision_fallback_on_uia_miss():
    frames = {
        "before": bytes([10, 10, 10, 255]) * 8,
        "after": bytes([180, 180, 180, 255]) * 8,
    }
    state = {"n": 0}

    def capture(_rect):
        state["n"] += 1
        return frames["before"] if state["n"] == 1 else frames["after"]

    last = _tree(True)
    step = a.run_hybrid_step(
        LoopAction("zoom", selector=Selector(automation_id="ID_ZOOM_EXTENTS")),
        snapshot=lambda: [],  # UIA miss this frame
        capture=capture,
        execute=lambda n, act: None,
        last_tree=last,
    )
    assert step.mode == "vision-fallback"
    assert step.status == a.PASSED


def test_hybrid_loop_read_skips_pixel_diff():
    step = a.run_hybrid_step(
        LoopAction("dim", kind="read", selector=Selector(automation_id="ID_ZOOM_EXTENTS")),
        snapshot=lambda: _tree(True),
        capture=lambda _r: (_ for _ in ()).throw(AssertionError("read must not capture")),
        execute=lambda n, act: (_ for _ in ()).throw(AssertionError("read must not execute")),
    )
    assert step.status == a.PASSED
    assert "Read-only" in step.detail
