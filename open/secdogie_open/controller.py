"""Pure-Python state layer for secdogie-open."""
from __future__ import annotations

import io
import queue
import threading
from dataclasses import dataclass, field

from secdogie_agent import config as config_mod
from secdogie_agent import screen
from secdogie_agent.providers import ANTHROPIC_PROVIDER_ID, DEFAULT_MODELS, OPENAI_PROVIDER_ID, SUGGESTED_MODELS, make_provider
from secdogie_agent.providers.base import VisionProvider
from secdogie_agent.providers.model_registry import fetch_openrouter_catalog

from . import runner, windows

DEFAULT_MODEL = DEFAULT_MODELS[ANTHROPIC_PROVIDER_ID]
DEFAULT_MAX_STEPS = 50
THUMB_EDGE = 160
_PROVIDER_LABELS = {
    ANTHROPIC_PROVIDER_ID: "Anthropic (Claude)",
    OPENAI_PROVIDER_ID: "OpenAI (GPT)",
}


def model_catalog() -> dict:
    """Return a live, searchable-friendly OpenRouter catalogue for the web UI.

    OpenRouter ids are prefixed with ``openrouter/`` so selecting one always
    routes the request through OpenRouter instead of accidentally sending a
    vendor/model id to a direct provider SDK.
    """
    records = fetch_openrouter_catalog()
    models = []
    for item in records:
        model_id = item["id"]
        models.append({
            **item,
            "id": f"openrouter/{model_id}",
            "provider": model_id.split("/", 1)[0] if "/" in model_id else "other",
        })
    return {
        "default": "openrouter/" + DEFAULT_MODELS["openrouter"],
        "source": "openrouter",
        "models": models,
        "fallback": not any(item.get("context_length") for item in records),
        "legacy": {
            "providers": [
                {"id": pid, "label": _PROVIDER_LABELS[pid], "models": SUGGESTED_MODELS[pid]}
                for pid in (ANTHROPIC_PROVIDER_ID, OPENAI_PROVIDER_ID)
            ]
        },
    }


@dataclass(frozen=True)
class StartResult:
    started: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: str | None = None


class Controller:
    def __init__(self) -> None:
        self._windows: dict[str, windows.WindowInfo] = {}
        self._runs: dict[str, runner.WindowRun] = {}
        self._status: dict[str, tuple[str, str]] = {}
        self._status_queue: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self._lock = threading.Lock()
        self._drain_thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._drain_thread.start()

    def _drain_loop(self) -> None:
        while True:
            window_id, status, detail = self._status_queue.get()
            with self._lock:
                self._status[window_id] = (status, detail)

    def refresh_windows(self) -> list[windows.WindowInfo]:
        found = windows.list_windows()
        with self._lock:
            self._windows = {w.id: w for w in found}
        return found

    def thumbnail_png(self, window_id: str) -> bytes | None:
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

    def start(
        self,
        window_ids: list[str],
        task: str,
        model: str = "",
        max_steps: int = DEFAULT_MAX_STEPS,
        auto: bool = False,
        api_key: str = "",
        models: list[str] | None = None,
    ) -> StartResult:
        task = task.strip()
        if not task:
            return StartResult(error="Enter a task first.")
        if not window_ids:
            return StartResult(error="Select at least one window.")

        selected_models = [str(m).strip() for m in (models or [model]) if str(m).strip()]
        if not selected_models:
            return StartResult(error="Select at least one model.")

        # A key typed into the web UI wins; blank falls back to env/config.
        # All live catalogue selections are OpenRouter refs, so one OpenRouter
        # key can be used to compare several vendors in one batch.
        started: list[str] = []
        skipped: list[str] = []
        with self._lock:
            for model_id in selected_models:
                resolved = config_mod.resolve(
                    cli_api_key=api_key.strip() or None,
                    cli_model=model_id,
                )
                if not resolved.api_key:
                    return StartResult(
                        error=f"No API key found for the {resolved.provider} provider. "
                        f"Paste one in the API key field or set {resolved.env_var}."
                    )
                for window_id in window_ids:
                    win = self._windows.get(window_id)
                    run_key = f"{window_id}\0{resolved.model}"
                    existing = self._runs.get(run_key)
                    if win is None or (existing is not None and existing.is_alive()):
                        skipped.append(run_key)
                        continue
                    self._status[window_id] = ("running", f"{resolved.model}: starting")

                    def provider_factory(resolved=resolved) -> VisionProvider:
                        return make_provider(resolved.provider, resolved.model, resolved.api_key)

                    self._runs[run_key] = runner.launch(
                        win,
                        provider_factory,
                        task,
                        auto=auto,
                        dry_run=not auto,
                        max_steps=max_steps,
                        status_queue=self._status_queue,
                        model_label=resolved.model,
                    )
                    started.append(run_key)
        return StartResult(started=started, skipped=skipped)

    def stop_all(self) -> list[str]:
        stopped: list[str] = []
        with self._lock:
            for run_key, run in self._runs.items():
                if run.is_alive():
                    run.stop()
                    stopped.append(run_key)
        return stopped

    def status_snapshot(self) -> dict[str, tuple[str, str]]:
        with self._lock:
            return dict(self._status)
