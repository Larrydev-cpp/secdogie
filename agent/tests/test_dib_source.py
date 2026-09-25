"""DIB observation: the bridge from native atlas_inspect into the agent.

Unit tests drive `inspect_dibs` with a fake runner; loop tests check that a DIB
reading reaches the model, counts as progress for stall detection and lands in
the trace; the end-to-end test reads a real bitmap out of a live `atlas_target`
process (skipped where the native binaries aren't built)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from secdogie_agent import actions, dib_source, loop, screen
from secdogie_agent.dib_source import DibReading, DibWatcher, inspect_dibs, observations_for
from secdogie_agent.observation import Geometry, SemanticNode, VisualReference, fuse, observe_ax
from secdogie_agent.providers.base import Action, VisionProvider


def _dib(w=160, h=96, bits=32, fill=0, address=0x1000):
    pix = bytes([fill]) * (w * h * 4)
    return {"address": address, "width": w, "height": h, "bit_count": bits, "compression": 0,
            "source": "heap", "preview": base64.b64encode(pix).decode()}


def _runner(doc=None, *, stdout=None, returncode=0, stderr="", raises=None, seen=None):
    def run(cmd, **kw):
        if seen is not None:
            seen.append(cmd)
        if raises is not None:
            raise raises
        out = stdout if stdout is not None else json.dumps(doc)
        return SimpleNamespace(stdout=out, stderr=stderr, returncode=returncode)
    return run


# --- inspect_dibs ----------------------------------------------------------


def test_reads_dibs_from_inspector_json():
    seen = []
    r = inspect_dibs(42, inspector="/x/atlas_inspect", platform="linux",
                     runner=_runner({"ok": True, "stats": {"regions_read": 3}, "dibs": [_dib()]}, seen=seen))
    assert r.ok and len(r.refs) == 1
    ref = r.refs[0]
    assert (ref.width, ref.height, ref.bit_count, ref.pixels_available) == (160, 96, 32, True)
    assert seen[0][:5] == ["/x/atlas_inspect", "--pid", "42", "--json", "--max-mb"]
    assert "160x96 32bpp" in r.summary()


def test_failures_come_back_as_reasons_not_exceptions():
    kw = {"inspector": "/x/atlas_inspect", "platform": "linux"}
    assert "timed out" in inspect_dibs(1, runner=_runner(raises=subprocess.TimeoutExpired("x", 1)), **kw).reason
    assert "could not run" in inspect_dibs(1, runner=_runner(raises=OSError("nope")), **kw).reason
    assert "exit 3" in inspect_dibs(1, runner=_runner(stdout="garbage", returncode=3, stderr="boom"), **kw).reason
    refused = inspect_dibs(1, runner=_runner({"ok": False, "detail": "access denied"}), **kw)
    assert not refused.ok and "access denied" in refused.reason and refused.refs == ()


def test_missing_inspector_and_macos(monkeypatch):
    monkeypatch.setattr(dib_source, "find_inspector", lambda: None)
    assert "not found" in inspect_dibs(1, platform="linux").reason
    called = []
    mac = inspect_dibs(1, inspector="/x", platform="darwin", runner=_runner({}, seen=called))
    assert "AX tree" in mac.reason and called == []  # never runs the inspector on macOS


def test_unreadable_memory_is_reported_not_called_empty():
    doc = {"ok": True, "detail": "VAD list empty: /proc/<pid>/maps unreadable", "stats": {"regions_read": 0},
           "dibs": []}
    r = inspect_dibs(1, inspector="/x", platform="linux", runner=_runner(doc))
    assert not r.ok and "memory not readable" in r.reason


def test_over_budget_is_refused_not_truncated():
    from secdogie_agent.observation import Budget
    r = inspect_dibs(1, inspector="/x", platform="linux", budget=Budget(max_dib_bytes=1000),
                     runner=_runner({"ok": True, "stats": {"regions_read": 3}, "dibs": [_dib()]}))
    assert not r.ok and "dib_processing_budget" in r.reason and r.refs == ()


def test_watcher_reports_change():
    seq = [DibReading(7, (VisualReference.from_dib_json(_dib(fill=0)),)),
           DibReading(7, (VisualReference.from_dib_json(_dib(fill=0)),)),
           DibReading(7, (VisualReference.from_dib_json(_dib(fill=9)),)),
           DibReading(7, reason="gone")]
    w = DibWatcher(7, inspect=lambda pid: seq.pop(0))
    assert w.step()[1] is None      # first reading: nothing to compare with
    assert w.step()[1] is False     # same pixels
    assert w.step()[1] is True      # pixels changed
    assert w.step()[1] is None      # failed read


# --- the loop --------------------------------------------------------------


class RecordingProvider(VisionProvider):
    def __init__(self, script):
        self.script = list(script)
        self.tasks: list[str] = []

    def next_action(self, task, screenshot_png, screen_size, history):
        self.tasks.append(task)
        return Action.from_dict(self.script.pop(0))


@pytest.fixture
def fake_screen(monkeypatch):
    executed = []
    monkeypatch.setattr(screen, "capture_screenshot", lambda region=None: (b"same-frame", (800, 600)))
    monkeypatch.setattr(screen, "prepare_for_model", lambda raw, size, **kw: (raw, size, 1.0))
    monkeypatch.setattr(actions, "execute", lambda action, **kw: executed.append(action.kind) or "ok")
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    return executed


def _readings(monkeypatch, fills):
    seq = list(fills)

    def fake(pid):
        fill = seq.pop(0) if seq else fills[-1]
        return DibReading(pid, (VisualReference.from_dib_json(_dib(fill=fill)),))
    monkeypatch.setattr(dib_source, "inspect_dibs", fake)


def test_model_sees_the_dib_summary(monkeypatch, fake_screen):
    _readings(monkeypatch, [1, 2])
    provider = RecordingProvider([{"action": "left_click", "x": 1, "y": 1}, {"action": "done", "text": "ok"}])
    assert loop.run(provider, loop.AgentConfig(task="t", auto=True, max_steps=5, dib_pid=99)) == 0
    assert "dib: 1 bitmap(s) 160x96 32bpp" in provider.tasks[0]
    assert "changed since last step" in provider.tasks[1]


def test_bitmap_change_counts_as_progress(monkeypatch, fake_screen):
    clicks = [{"action": "left_click", "x": 5, "y": 5}] * 6 + [{"action": "done", "text": "ok"}]
    # Same click on an unchanged screen stalls (exit 6) ...
    _readings(monkeypatch, [0])
    rc_still = loop.run(RecordingProvider(clicks), loop.AgentConfig(task="t", auto=True, max_steps=10,
                                                                    stall_limit=3, dib_pid=99))
    assert rc_still == 6
    # ... but not when the canvas changes each step.
    _readings(monkeypatch, [1, 2, 3, 4, 5, 6, 7])
    rc_moving = loop.run(RecordingProvider(clicks), loop.AgentConfig(task="t", auto=True, max_steps=10,
                                                                     stall_limit=3, dib_pid=99))
    assert rc_moving == 0


def test_trace_records_the_bitmaps(monkeypatch, fake_screen):
    _readings(monkeypatch, [4])
    entries = []
    provider = RecordingProvider([{"action": "left_click", "x": 1, "y": 1}, {"action": "done", "text": "ok"}])
    loop.run(provider, loop.AgentConfig(task="t", auto=True, max_steps=5, dib_pid=99,
                                        trace_on_entry=entries.append))
    assert entries and entries[0].frame_sha256 != hashlib.sha256(b"same-frame").hexdigest()


def test_missing_inspector_leaves_the_loop_working(monkeypatch, fake_screen):
    monkeypatch.setattr(dib_source, "inspect_dibs", lambda pid: DibReading(pid, reason="atlas_inspect not found"))
    provider = RecordingProvider([{"action": "left_click", "x": 1, "y": 1}, {"action": "done", "text": "ok"}])
    assert loop.run(provider, loop.AgentConfig(task="t", auto=True, max_steps=5, dib_pid=99)) == 0
    assert fake_screen == ["left_click"]
    assert "dib: unavailable (atlas_inspect not found)" in provider.tasks[0]


# --- end to end: a real bitmap from a live process ----------------------------

_TARGET = Path(__file__).resolve().parents[2] / "native" / "atlas" / "build" / "atlas_target"


@pytest.mark.skipif(sys.platform in ("darwin", "win32"), reason="Linux end-to-end (macOS is AX-only)")
def test_reads_a_real_bitmap_from_a_live_process():
    # CI sets SECDOGIE_DIB_E2E_REQUIRED=1 so this can never pass by skipping there.
    required = os.environ.get("SECDOGIE_DIB_E2E_REQUIRED") == "1"
    if dib_source.find_inspector() is None or not _TARGET.is_file():
        if required:
            pytest.fail("native atlas_inspect / atlas_target not built")
        pytest.skip("native atlas_inspect / atlas_target not built")
    proc = subprocess.Popen([str(_TARGET)], stdout=subprocess.PIPE, text=True, start_new_session=True)
    try:
        pid = int(proc.stdout.readline().split()[0])
        reading = inspect_dibs(pid)
        if not reading.ok and "not readable" in reading.reason and not required:
            pytest.skip(f"this user may not read the target's memory: {reading.reason}")
        assert reading.ok, reading.reason
        shapes = [(r.width, r.height, r.bit_count) for r in reading.refs]
        assert (160, 96, 32) in shapes          # the viewport atlas_target planted
        assert all(r.pixels_available for r in reading.refs)

        # fuse with the same window's AX observation: both senses contribute
        dib_obs = observations_for(reading, window_id=7)
        ax = observe_ax(window_id=7, app_pid=pid, geometry=Geometry(0, 0, 160, 96),
                        semantic_nodes=[SemanticNode(role="Pane", name="viewport")])
        fused = fuse([ax, *dib_obs])
        assert {o.source for o in fused.contributors} == {"ax", "dib"}
        assert fused.clean

        # nothing changed in the process between two reads
        watcher = DibWatcher(pid)
        watcher.step()
        assert watcher.step()[1] is False
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
