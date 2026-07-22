"""Tests for DPI awareness (dpi.py). The real Windows SetProcessDpiAwareness*
calls are on-machine; what's proved here is the dispatch: the off-Windows no-op,
idempotency, the fallback-label selection through an injected platform layer,
and that a throwing platform layer can't crash the caller."""
import sys

import pytest
from secdogie_agent import dpi


@pytest.fixture(autouse=True)
def _reset_dpi_status(monkeypatch):
    # dpi caches its one real attempt in a module global; reset it around every
    # test so idempotency doesn't leak between them.
    monkeypatch.setattr(dpi, "_STATUS", None)


def test_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(dpi.sys, "platform", "linux")
    assert dpi.ensure_dpi_awareness() == "not-windows"
    assert dpi.current_status() == "not-windows"


def test_is_idempotent_only_the_first_call_applies(monkeypatch):
    monkeypatch.setattr(dpi.sys, "platform", "win32")
    calls = {"n": 0}

    def apply():
        calls["n"] += 1
        return "per-monitor-v2"

    assert dpi.ensure_dpi_awareness(_apply=apply) == "per-monitor-v2"
    # A second call (e.g. another entry point) must not re-apply: the first
    # DPI declaration in a process wins on Windows anyway.
    assert dpi.ensure_dpi_awareness(_apply=apply) == "already-set"
    assert calls["n"] == 1


def test_reports_the_label_the_platform_layer_returns(monkeypatch):
    monkeypatch.setattr(dpi.sys, "platform", "win32")
    for label in ("per-monitor-v2", "per-monitor", "system", "unavailable"):
        monkeypatch.setattr(dpi, "_STATUS", None)
        assert dpi.ensure_dpi_awareness(_apply=lambda label=label: label) == label


def test_a_throwing_platform_layer_never_crashes_the_caller(monkeypatch):
    monkeypatch.setattr(dpi.sys, "platform", "win32")

    def boom():
        raise OSError("SetProcessDpiAwarenessContext failed")

    assert dpi.ensure_dpi_awareness(_apply=boom) == "unavailable"


def test_off_windows_the_real_apply_is_never_reached(monkeypatch):
    # On the (non-Windows) CI box, the real path must return not-windows without
    # touching ctypes at all.
    if sys.platform.startswith("win"):
        return
    assert dpi.ensure_dpi_awareness() == "not-windows"
