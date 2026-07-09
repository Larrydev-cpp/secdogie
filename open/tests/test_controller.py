import threading
import time

import pytest
from secdogie_open import controller as controller_mod
from secdogie_open import runner, windows
from secdogie_open.controller import Controller, StartResult


def _window(id_="w1", title="App"):
    return windows.WindowInfo(id=id_, title=title, left=10, top=20, width=300, height=400)


def _resolved(api_key="sk-test", provider="anthropic", model="claude-sonnet-5"):
    return controller_mod.config_mod.ResolvedConfig(
        api_key=api_key, model=model, provider=provider, env_var="ANTHROPIC_API_KEY", api_key_source="test"
    )


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# -- refresh_windows / thumbnail_png ---------------------------------------------------------

def test_refresh_windows_returns_and_caches(monkeypatch):
    found = [_window("w1"), _window("w2", "Other")]
    monkeypatch.setattr(controller_mod.windows, "list_windows", lambda: found)

    c = Controller()
    result = c.refresh_windows()
    assert result == found


def test_refresh_windows_propagates_backend_error(monkeypatch):
    def boom():
        raise windows.NoWindowBackendError("no display")

    monkeypatch.setattr(controller_mod.windows, "list_windows", boom)
    c = Controller()
    with pytest.raises(windows.NoWindowBackendError):
        c.refresh_windows()


def test_thumbnail_png_unknown_window_returns_none():
    c = Controller()
    assert c.thumbnail_png("nope") is None


def test_thumbnail_png_returns_valid_png(monkeypatch):
    win = _window()
    monkeypatch.setattr(controller_mod.windows, "list_windows", lambda: [win])

    def fake_capture(region):
        assert region == win.region
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (400, 300), (10, 20, 30)).save(buf, format="PNG")
        return buf.getvalue(), (400, 300)

    monkeypatch.setattr(controller_mod.screen, "capture_screenshot", fake_capture)

    c = Controller()
    c.refresh_windows()
    png = c.thumbnail_png(win.id)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_thumbnail_png_capture_failure_returns_none(monkeypatch):
    win = _window()
    monkeypatch.setattr(controller_mod.windows, "list_windows", lambda: [win])
    monkeypatch.setattr(
        controller_mod.screen, "capture_screenshot", lambda region: (_ for _ in ()).throw(RuntimeError("no display"))
    )

    c = Controller()
    c.refresh_windows()
    assert c.thumbnail_png(win.id) is None


# -- start ---------------------------------------------------------

def test_start_rejects_empty_task():
    c = Controller()
    result = c.start(window_ids=["w1"], task="   ", model="", max_steps=5, auto=False)
    assert result == StartResult(error="Enter a task first.")


def test_start_rejects_no_windows_selected():
    c = Controller()
    result = c.start(window_ids=[], task="do it", model="", max_steps=5, auto=False)
    assert result.error == "Select at least one window."


def test_start_rejects_missing_api_key(monkeypatch):
    monkeypatch.setattr(
        controller_mod.config_mod,
        "resolve",
        lambda **kw: controller_mod.config_mod.ResolvedConfig(
            api_key=None, model="claude-sonnet-5", provider="anthropic", env_var="ANTHROPIC_API_KEY", api_key_source="none"
        ),
    )
    c = Controller()
    result = c.start(window_ids=["w1"], task="do it", model="", max_steps=5, auto=False)
    assert result.error is not None
    assert "ANTHROPIC_API_KEY" in result.error


def test_start_launches_selected_windows_and_reports_done(monkeypatch):
    win = _window("w1")
    monkeypatch.setattr(controller_mod.windows, "list_windows", lambda: [win])
    monkeypatch.setattr(controller_mod.config_mod, "resolve", lambda **kw: _resolved())
    monkeypatch.setattr(controller_mod, "make_provider", lambda provider, model, key: "the-provider")
    monkeypatch.setattr(runner, "run", lambda provider, config: 0)

    c = Controller()
    c.refresh_windows()
    result = c.start(window_ids=[win.id], task="click the button", model="", max_steps=5, auto=True)

    assert result.started == [win.id]
    assert result.skipped == []
    assert result.error is None

    c._runs[win.id].thread.join(timeout=2)
    assert _wait_until(lambda: c.status_snapshot().get(win.id) == ("done", "done"))


def test_start_skips_a_window_that_is_still_running(monkeypatch):
    win = _window("w1")
    monkeypatch.setattr(controller_mod.windows, "list_windows", lambda: [win])
    monkeypatch.setattr(controller_mod.config_mod, "resolve", lambda **kw: _resolved())
    monkeypatch.setattr(controller_mod, "make_provider", lambda provider, model, key: "the-provider")

    release = threading.Event()

    def blocking_run(provider, config):
        release.wait(timeout=2)
        return 0

    monkeypatch.setattr(runner, "run", blocking_run)

    c = Controller()
    c.refresh_windows()
    first = c.start(window_ids=[win.id], task="task 1", model="", max_steps=5, auto=True)
    assert first.started == [win.id]

    second = c.start(window_ids=[win.id], task="task 2", model="", max_steps=5, auto=True)
    assert second.started == []
    assert second.skipped == [win.id]

    release.set()
    c._runs[win.id].thread.join(timeout=2)


def test_start_skips_an_unknown_window_id(monkeypatch):
    monkeypatch.setattr(controller_mod.config_mod, "resolve", lambda **kw: _resolved())
    c = Controller()  # no refresh_windows() -> "w1" is unknown
    result = c.start(window_ids=["w1"], task="do it", model="", max_steps=5, auto=True)
    assert result.started == []
    assert result.skipped == ["w1"]


# -- stop_all ---------------------------------------------------------

def test_stop_all_signals_running_windows_and_marks_stopping(monkeypatch):
    win = _window("w1")
    monkeypatch.setattr(controller_mod.windows, "list_windows", lambda: [win])
    monkeypatch.setattr(controller_mod.config_mod, "resolve", lambda **kw: _resolved())
    monkeypatch.setattr(controller_mod, "make_provider", lambda provider, model, key: "the-provider")

    started_running = threading.Event()
    seen_stop = {}

    def fake_run(provider, config):
        started_running.set()
        while not config.should_stop():
            time.sleep(0.01)
        seen_stop["stopped"] = True
        return 5

    monkeypatch.setattr(runner, "run", fake_run)

    c = Controller()
    c.refresh_windows()
    c.start(window_ids=[win.id], task="task", model="", max_steps=5, auto=True)
    assert started_running.wait(timeout=2)

    stopped = c.stop_all()
    assert stopped == [win.id]
    assert c.status_snapshot()[win.id] == ("stopping", "")

    c._runs[win.id].thread.join(timeout=2)
    assert seen_stop.get("stopped") is True


def test_stop_all_with_nothing_running_returns_empty():
    c = Controller()
    assert c.stop_all() == []


# -- status_snapshot ---------------------------------------------------------

def test_status_snapshot_is_a_copy_not_a_live_view():
    c = Controller()
    snap = c.status_snapshot()
    snap["w1"] = ("running", "hack")
    assert c.status_snapshot() == {}
