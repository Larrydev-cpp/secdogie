"""Pure-Python state layer: the same "detect windows, launch one agent per
selection, track status, stop all" logic the old tkinter App class held,
minus any GUI toolkit. server.py is a thin HTTP/JSON shell around this; this
module has no import on http.server or any GUI, so it's unit-testable
directly and reusable if a different front end (or a future key-pool
dispatcher) wants the same operations without going through HTTP.
"""
from __future__ import annotations

import io
import queue
import threading
from dataclasses import dataclass, field

from secdogie_agent import config as config_mod
from secdogie_agent import screen
from secdogie_agent.providers import make_provider
from secdogie_agent.providers.base import VisionProvider

from . import runner, windows

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_STEPS = 50
THUMB_EDGE = 160


@dataclass(frozen=True)
class StartResult:
    started: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already running; not an error
    error: str | None = None  # set instead of started/skipped when the whole request is rejected


class Controller:
    def __init__(self) -> None:
        self._windows: dict[str, windows.WindowInfo] = {}
        self._runs: dict[str, runner.WindowRun] = {}
        self._status: dict[str, tuple[str, str]] = {}
        self._status_queue: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self._lock = threading.Lock()
        # Moves status_queue entries (posted by runner threads) into
        # self._status so status_snapshot() can return a point-in-time view
        # without draining the queue itself -- an HTTP poll that's missed or
        # coalesced must not lose a status update the way a one-shot queue
        # drain would.
        self._drain_thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._drain_thread.start()

    def _drain_loop(self) -> None:
        while True:
            window_id, status, detail = self._status_queue.get()
            with self._lock:
                self._status[window_id] = (status, detail)

    # -- window list ---------------------------------------------------------
    def refresh_windows(self) -> list[windows.WindowInfo]:
        """Raises windows.NoWindowBackendError if windows can't be listed at
        all -- callers should surface that message, not an empty list."""
        found = windows.list_windows()
        with self._lock:
            self._windows = {w.id: w for w in found}
        return found

    def thumbnail_png(self, window_id: str) -> bytes | None:
        """A small PNG of the window's current region, or None if the window
        is unknown or the capture fails -- a thumbnail is a nicety, callers
        should degrade gracefully (e.g. a placeholder image) rather than error."""
        with self._lock:
            win = self._windows.get(window_id)
        if win is None:
            return None
        try:
            from PIL import Image

            png, _size = screen.capture_screenshot(region=win.region)
            img = Image.open(io.BytesIO(png))
            img.thumbnail((THUMB_EDGE, THUMB_EDGE))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None

    # -- running ---------------------------------------------------------
    def start(
        self,
        window_ids: list[str],
        task: str,
        model: str,
        max_steps: int,
        auto: bool,
    ) -> StartResult:
        task = task.strip()
        if not task:
            return StartResult(error="Enter a task first.")
        if not window_ids:
            return StartResult(error="Select at least one window.")

        resolved = config_mod.resolve(cli_model=model or None)
        if not resolved.api_key:
            return StartResult(
                error=f"No API key found for the {resolved.provider} provider. Set "
                f"{resolved.env_var} or fill in a secdogie-agent config file, then retry."
            )

        def provider_factory() -> VisionProvider:
            return make_provider(resolved.provider, resolved.model, resolved.api_key)

        started: list[str] = []
        skipped: list[str] = []
        with self._lock:
            for window_id in window_ids:
                win = self._windows.get(window_id)
                existing = self._runs.get(window_id)
                if win is None or (existing is not None and existing.is_alive()):
                    skipped.append(window_id)
                    continue
                self._status[window_id] = ("running", "starting")
                self._runs[window_id] = runner.launch(
                    win,
                    provider_factory,
                    task,
                    auto=auto,
                    dry_run=not auto,
                    max_steps=max_steps,
                    status_queue=self._status_queue,
                )
                started.append(window_id)
        return StartResult(started=started, skipped=skipped)

    def stop_all(self) -> list[str]:
        stopped: list[str] = []
        with self._lock:
            for window_id, run in self._runs.items():
                if run.is_alive():
                    run.stop()
                    self._status[window_id] = ("stopping", "")
                    stopped.append(window_id)
        return stopped

    def status_snapshot(self) -> dict[str, tuple[str, str]]:
        with self._lock:
            return dict(self._status)
