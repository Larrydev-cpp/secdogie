"""Run one secdogie-agent loop per selected window/model pair."""
from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from secdogie_agent.backend import DesktopBackend
from secdogie_agent.loop import AgentConfig, run
from secdogie_agent.providers.base import VisionProvider

from . import windows
from .windows import WindowInfo

RunStatus = str
_CLEAN_EXIT_CODES = {0: ("done", "done"), 3: ("done", "gave up: reached max_steps without finishing"), 5: ("stopped", "stopped")}


@dataclass
class WindowRun:
    window: WindowInfo
    thread: threading.Thread
    _stop_event: threading.Event = field(repr=False)

    def stop(self) -> None: self._stop_event.set()
    def is_alive(self) -> bool: return self.thread.is_alive()


def launch(window: WindowInfo, provider_factory: Callable[[], VisionProvider], task: str, *, auto: bool,
           dry_run: bool, max_steps: int, status_queue: queue.Queue[tuple[str, RunStatus, str]],
           model_label: str = "") -> WindowRun:
    stop_event = threading.Event()

    def post(status: RunStatus, detail: str, include_model: bool = False) -> None:
        if include_model and model_label: detail = f"{model_label}: {detail}"
        status_queue.put((window.id, status, detail))

    def body() -> None:
        post("running", "starting", include_model=True)
        try: provider = provider_factory()
        except Exception as e: post("error", f"could not set up provider: {e}"); return
        backend = DesktopBackend(activate=lambda: windows.focus_window(window), window_handle=window.handle)
        config = AgentConfig(task=task, max_steps=max_steps, auto=auto, dry_run=dry_run,
                             confirm_high_risk=False, region=window.region,
                             logger_name=f"secdogie_open.{window.id}", should_stop=stop_event.is_set, backend=backend)
        try: rc = run(provider, config)
        except Exception as e: post("error", str(e)); return
        if rc in _CLEAN_EXIT_CODES:
            status, detail = _CLEAN_EXIT_CODES[rc]; post(status, detail)
        else: post("error", f"agent loop exited with code {rc}")

    thread = threading.Thread(target=body, name=f"secdogie-open:{window.id}:{model_label or 'model'}", daemon=True)
    handle = WindowRun(window=window, thread=thread, _stop_event=stop_event)
    thread.start(); return handle
