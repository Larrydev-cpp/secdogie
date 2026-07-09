"""The seam between the agent loop and whatever it's driving.

The loop's job -- screenshot, ask the model for one action, confirm, execute,
feed the result back -- is the same whether the target is this machine's
desktop or a phone on the end of a USB cable. What differs is only *how* you
grab a screenshot and *how* you carry out an action. That pair (plus a
one-time setup hook) is a Backend.

`DesktopBackend` is the default and preserves the original behavior exactly:
mss for capture, pyautogui for input. Other targets (e.g. an Android device
over adb) ship their own Backend in their own package and hand it to the loop
via `AgentConfig.backend`, reusing everything else unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from . import actions, screen
from .providers.base import Action


@dataclass(frozen=True)
class ElementSelector:
    """A backend-defined "find this on-screen target again" description, used
    by macro.py to replay a click by identity instead of a frozen pixel
    coordinate. `kind` names which backend produced it (e.g.
    "android-uiautomator") so a selector is never handed to an unrelated
    backend's `locate()` by mistake; `attrs` is whatever that backend needs to
    re-find the same element (e.g. resource id, text, class) -- the macro
    engine never interprets `attrs` itself, only round-trips it."""

    kind: str
    attrs: dict[str, Any]


@runtime_checkable
class Locatable(Protocol):
    """Optional Backend capability: convert an absolute point to a
    re-locatable selector, and resolve a selector back to a current point.
    Implement this when the target exposes an accessibility tree/UI
    automation API (see android's AdbBackend). A backend that can't identify
    elements (e.g. plain desktop pixels today) just doesn't implement this;
    callers check `isinstance(backend, Locatable)` and fall back to a
    resolution-independent normalized coordinate instead."""

    def describe_target(self, x: int, y: int) -> ElementSelector | None:
        """Best-effort: describe whatever real element is at (x, y) so
        `locate` can find it again later. None if nothing identifiable is
        there (the caller then falls back to a raw/normalized coordinate)."""
        ...

    def locate(self, selector: ElementSelector) -> tuple[int, int] | None:
        """Best-effort: resolve a previously-recorded selector back to a
        current (x, y). None if it can't be found (the UI changed)."""
        ...


class Backend(Protocol):
    """A target the agent can drive. Coordinates handed to `execute` are real
    target pixels (the loop has already mapped them out of model space)."""

    def setup(self, logger) -> None:
        """One-time preparation before the loop starts (e.g. arm a fail-safe,
        verify the device is reachable). Should not raise for a merely
        degraded target -- log a warning and let --dry-run still work."""

    def capture(self, region: tuple[int, int, int, int] | None):
        """Return (png_bytes, (width, height)). Raise screen.CaptureError if a
        screenshot can't be taken. `region` is an optional (left, top, width,
        height) crop; backends without a region concept ignore it."""

    def execute(self, action: Action) -> str:
        """Carry out one action, returning a short human-readable result."""


class DesktopBackend:
    """Drives the local desktop: mss screenshots, pyautogui input. This is the
    original, default target -- keeping it a Backend just lets other targets
    slot in beside it without the loop knowing the difference."""

    def __init__(
        self,
        move_duration: float = actions.DEFAULT_MOVE_DURATION,
        settle: float = actions.DEFAULT_SETTLE,
    ):
        self.move_duration = move_duration
        self.settle = settle

    def setup(self, logger) -> None:
        try:
            import pyautogui

            pyautogui.FAILSAFE = True  # slamming the cursor into a screen corner aborts pyautogui calls
        except Exception as e:
            # Not just ImportError: pyautogui's own import chain (mouseinfo) raises other
            # exceptions (e.g. KeyError on DISPLAY) when there's no GUI session at all.
            logger.warning("pyautogui unavailable (%s); only --dry-run will work", e)

    def capture(self, region: tuple[int, int, int, int] | None):
        return screen.capture_screenshot(region=region)

    def execute(self, action: Action) -> str:
        return actions.execute(action, move_duration=self.move_duration, settle=self.settle)
