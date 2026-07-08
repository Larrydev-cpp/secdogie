import queue

from secdogie_open import runner, windows


def _window():
    return windows.WindowInfo(id="w1", title="App", left=10, top=20, width=300, height=400)


def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


def test_launch_scopes_config_to_window_and_reports_done(monkeypatch):
    captured = {}

    def fake_run(provider, config):
        captured["config"] = config
        captured["provider"] = provider
        return 0

    monkeypatch.setattr(runner, "run", fake_run)
    win = _window()
    q = queue.Queue()
    provider_calls = []

    def factory():
        provider_calls.append(1)
        return "the-provider"

    run_handle = runner.launch(
        win, factory, "do the thing", auto=True, dry_run=False, max_steps=7, status_queue=q
    )
    run_handle.thread.join(timeout=2)

    assert not run_handle.thread.is_alive()
    assert provider_calls == [1]
    assert captured["provider"] == "the-provider"

    cfg = captured["config"]
    assert cfg.task == "do the thing"
    assert cfg.region == (10, 20, 300, 400)
    assert cfg.max_steps == 7
    assert cfg.auto is True
    assert cfg.dry_run is False
    assert cfg.logger_name == "secdogie_open.w1"
    assert callable(cfg.should_stop)

    statuses = _drain(q)
    assert statuses[0] == (win.id, "running", "starting")
    assert statuses[-1] == (win.id, "done", "done")


def test_launch_max_steps_exit_reported_distinctly_from_done(monkeypatch):
    monkeypatch.setattr(runner, "run", lambda provider, config: 3)
    win = _window()
    q = queue.Queue()
    run_handle = runner.launch(win, lambda: None, "task", auto=True, dry_run=False, max_steps=1, status_queue=q)
    run_handle.thread.join(timeout=2)

    status, detail = _drain(q)[-1][1:]
    assert status == "done"
    assert "max_steps" in detail


def test_stop_sets_the_should_stop_flag_the_loop_checks(monkeypatch):
    seen = {}

    def fake_run(provider, config):
        seen["should_stop"] = config.should_stop
        assert config.should_stop() is False  # not stopped yet when the loop starts
        return 5

    monkeypatch.setattr(runner, "run", fake_run)
    win = _window()
    q = queue.Queue()
    run_handle = runner.launch(win, lambda: None, "task", auto=True, dry_run=False, max_steps=1, status_queue=q)
    run_handle.thread.join(timeout=2)

    run_handle.stop()
    assert seen["should_stop"]() is True
    assert _drain(q)[-1] == (win.id, "stopped", "stopped")


def test_provider_factory_error_reports_error_status_not_a_crash(monkeypatch):
    def bad_factory():
        raise RuntimeError("no api key configured")

    win = _window()
    q = queue.Queue()
    run_handle = runner.launch(win, bad_factory, "task", auto=True, dry_run=False, max_steps=1, status_queue=q)
    run_handle.thread.join(timeout=2)

    window_id, status, detail = _drain(q)[-1]
    assert status == "error"
    assert "no api key configured" in detail


def test_agent_loop_exception_reports_error_status_not_a_crash(monkeypatch):
    monkeypatch.setattr(runner, "run", lambda provider, config: (_ for _ in ()).throw(RuntimeError("boom")))
    win = _window()
    q = queue.Queue()
    run_handle = runner.launch(win, lambda: None, "task", auto=True, dry_run=False, max_steps=1, status_queue=q)
    run_handle.thread.join(timeout=2)

    assert _drain(q)[-1] == (win.id, "error", "boom")


def test_unexpected_exit_code_reports_error_status(monkeypatch):
    monkeypatch.setattr(runner, "run", lambda provider, config: 4)  # e.g. no display
    win = _window()
    q = queue.Queue()
    run_handle = runner.launch(win, lambda: None, "task", auto=True, dry_run=False, max_steps=1, status_queue=q)
    run_handle.thread.join(timeout=2)

    window_id, status, detail = _drain(q)[-1]
    assert status == "error"
    assert "4" in detail
