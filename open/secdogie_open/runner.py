"""Runs one secdogie-agent loop per selected window, in its own thread,
each scoped to that window's screen region so several windows can be
driven at once instead of one agent owning the whole screen.

All windows share whatever single provider/API key the caller resolved --
today that's the only option secdogie-agent supports. Concurrency here is
about *scope* (one window each) not about spreading load across several
API keys; that's future work this lays the groundwork for (see the
project's multi-key plan), which is why each window gets its own
VisionProvider instance from `provider_factory` rather than one shared
instance -- that's the seam a future key pool would hand out from.
"""
from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from secdogie_agent.loop import AgentConfig, run
from secdogie_agent.providers.base import VisionProvider

from .windows import WindowInfo

# Closed set of states a window run can be in, posted to the status queue
# alongside the window id and a short human-readable detail string.
RunStatus = str  # one of: "running", "done", "error", "stopped"

# loop.run()'s process-style exit codes that mean "stopped on purpose", not
# "something went wrong" -- 5 is our own should_stop cancellation. Mapped to
# (status, detail); status stays a small closed set for the GUI to key off,
# detail carries the distinction (e.g. "done" vs "gave up at max_steps").
_CLEAN_EXIT_CODES = {
    0: ("done", "done"),
    3: ("done", "gave up: reached max_steps without finishing"),
    5: ("stopped", "stopped"),
}


@dataclass
class WindowRun:
    window: WindowInfo
    thread: threading.Thread
    _stop_event: threading.Event = field(repr=False)

    def stop(self) -> None:
        self._stop_event.set()

    def is_alive(self) -> bool:
        return self.thread.is_alive()


def launch(
    window: WindowInfo,
    provider_factory: Callable[[], VisionProvider],
    task: str,
    *,
    auto: bool,
    dry_run: bool,
    max_steps: int,
    status_queue: "queue.Queue[tuple[str, RunStatus, str]]",
) -> WindowRun:
    """Starts a daemon thread running one agent loop scoped to `window`.

    Status updates are posted to `status_queue` as (window.id, status,
    detail) tuples; the GUI polls that queue on its own thread rather than
    touching tkinter widgets from a worker thread.
    """
    stop_event = threading.Event()

    def body() -> None:
        status_queue.put((window.id, "running", "starting"))
        try:
            provider = provider_factory()
        except Exception as e:
            status_queue.put((window.id, "error", f"could not set up provider: {e}"))
            return

        config = AgentConfig(
            task=task,
            max_steps=max_steps,
            auto=auto,
            dry_run=dry_run,
            region=window.region,
            logger_name=f"secdogie_open.{window.id}",
            should_stop=stop_event.is_set,
        )
        try:
            rc = run(provider, config)
        except Exception as e:
            status_queue.put((window.id, "error", str(e)))
            return

        if rc in _CLEAN_EXIT_CODES:
            status, detail = _CLEAN_EXIT_CODES[rc]
            status_queue.put((window.id, status, detail))
        else:
            status_queue.put((window.id, "error", f"agent loop exited with code {rc}"))

    thread = threading.Thread(target=body, name=f"secdogie-open:{window.id}", daemon=True)
    run_handle = WindowRun(window=window, thread=thread, _stop_event=stop_event)
    thread.start()
    return run_handle
