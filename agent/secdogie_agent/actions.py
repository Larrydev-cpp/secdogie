"""Executes a validated Action against the real mouse/keyboard via pyautogui.

`done` and `ask_user` are handled by the agent loop, not here -- they end
or pause the loop rather than performing an OS-level action.

Movement is deliberately not instantaneous: teleporting the cursor and
clicking in the same tick makes some apps miss hover/focus events. We move
over a short duration and pause briefly before clicking, which is both more
reliable and closer to human input.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable

from .providers.base import Action

DEFAULT_MOVE_DURATION = 0.15
DEFAULT_SETTLE = 0.05
CLIPBOARD_SETTLE = 0.15

_CLIPBOARD_HELP = (
    "typing non-ASCII text (e.g. Chinese) needs clipboard access. Install "
    "the pyperclip backend for your OS: on Linux `sudo apt install xclip` "
    "(or xsel); pyperclip is bundled and works out of the box on Windows/macOS. "
    "underlying error: {error}"
)

_INPUT_LOCK = threading.Lock()
_NON_INPUT_KINDS = frozenset({"wait", "screenshot", "open"})
_NULL_CTX = contextlib.nullcontext()

FOCUS_UNCONFIRMED_NOTE = " [focus unconfirmed: the target window may not have been frontmost]"

HIGH_RISK_KINDS = frozenset({"open", "run_elevated"})

# pyautogui on Darwin posts CGEvent / IOHID. Mutation there is AXPress /
# AXValue only — never a synthesized mouse/key HID event.
_DARWIN_HID_KINDS = frozenset({
    "left_click", "right_click", "double_click", "move", "drag",
    "scroll", "track_click", "type", "key", "hold_key",
})
_DARWIN_HID_REFUSED = (
    "macOS mutation is AXPress/AXValue only; HID/CGEvent/IOHID/pyautogui refused."
)


def is_high_risk(action: Action) -> bool:
    """True for actions that can permanently change files or close apps.

    Used by the loop to force a confirmation even under --auto. Covers the
    explicit HIGH_RISK_KINDS plus common save / delete / close key combos that
    CAD and document apps use (Ctrl+S, Delete, Alt+F4, Ctrl+W).
    """
    if action.kind in HIGH_RISK_KINDS:
        return True
    if action.kind == "key" and action.keys:
        keys_lower = {k.lower() for k in action.keys}
        has_mod = bool(keys_lower & {"ctrl", "control", "command", "cmd", "alt", "option"})
        if "s" in keys_lower and has_mod:
            return True
        if keys_lower & {"delete", "backspace"}:
            return True
        if "f4" in keys_lower and bool(keys_lower & {"alt", "option"}):
            return True
        if "w" in keys_lower and bool(keys_lower & {"ctrl", "control", "command", "cmd"}):
            return True
    return False


# Kinds that never change app state — allowed under --read-only.
_READ_ONLY_SAFE_KINDS = frozenset({
    "wait", "screenshot", "look", "done", "ask_user", "remember",
    "left_click", "right_click", "double_click", "move", "scroll", "track_click",
    "click_element",
})


def is_mutating(action: Action) -> bool:
    """True for actions that can change files, text, or document state.

    Used by --read-only: navigation clicks/scrolls stay allowed so the agent can
    still *look* at a CAD drawing; typing, hotkeys, drag, open, and elevation
    are blocked. High-risk save/delete/close keys are always mutating.
    """
    if action.kind in _READ_ONLY_SAFE_KINDS:
        return False
    if action.kind in HIGH_RISK_KINDS:
        return True
    if action.kind in {"type", "key", "hold_key", "drag"}:
        return True
    return is_high_risk(action)


def execute(
    action: Action,
    move_duration: float = DEFAULT_MOVE_DURATION,
    settle: float = DEFAULT_SETTLE,
    activate: Callable[[], bool] | None = None,
) -> str:
    """Execute one action, holding the shared input lock for mouse/keyboard."""
    guard = _NULL_CTX if action.kind in _NON_INPUT_KINDS else _INPUT_LOCK
    with guard:
        confirmed = True
        if activate is not None and action.kind not in _NON_INPUT_KINDS:
            confirmed = _activated(activate)
        result = _dispatch(action, move_duration, settle)
        return result if confirmed else result + FOCUS_UNCONFIRMED_NOTE


def _activated(activate: Callable[[], bool]) -> bool:
    try:
        return activate() is not False
    except Exception:
        return False


def _dispatch(action: Action, move_duration: float, settle: float) -> str:
    if sys.platform == "darwin" and action.kind in _DARWIN_HID_KINDS:
        return _DARWIN_HID_REFUSED

    import pyautogui

    def _approach(x: int, y: int) -> None:
        pyautogui.moveTo(x, y, duration=move_duration)
        if settle:
            time.sleep(settle)

    if action.kind == "left_click":
        _approach(action.x, action.y)
        pyautogui.click(button="left")
        return f"clicked left at ({action.x}, {action.y})"
    elif action.kind == "right_click":
        _approach(action.x, action.y)
        pyautogui.click(button="right")
        return f"clicked right at ({action.x}, {action.y})"
    elif action.kind == "double_click":
        _approach(action.x, action.y)
        pyautogui.doubleClick()
        return f"double-clicked at ({action.x}, {action.y})"
    elif action.kind == "move":
        pyautogui.moveTo(action.x, action.y, duration=move_duration)
        return f"moved cursor to ({action.x}, {action.y})"
    elif action.kind == "drag":
        _approach(action.x, action.y)
        pyautogui.dragTo(action.to_x, action.to_y, duration=max(move_duration, 0.2), button="left")
        return f"dragged from ({action.x}, {action.y}) to ({action.to_x}, {action.to_y})"
    elif action.kind == "type":
        text = action.text or ""
        if text.isascii():
            pyautogui.typewrite(text, interval=0.02)
            return f"typed {len(text)} character(s)"
        _paste_text(text)
        return f"typed {len(text)} character(s) via clipboard (non-ASCII)"
    elif action.kind == "key":
        keys = action.keys or []
        if len(keys) == 1:
            pyautogui.press(keys[0])
        elif len(keys) > 1:
            pyautogui.hotkey(*keys)
        return f"pressed key(s): {keys}"
    elif action.kind == "hold_key":
        keys = action.keys or []
        seconds = action.seconds if action.seconds is not None else 1.0
        for k in keys:
            pyautogui.keyDown(k)
        try:
            time.sleep(seconds)
        finally:
            for k in reversed(keys):
                pyautogui.keyUp(k)
        return f"held key(s) {keys} for {seconds}s"
    elif action.kind == "open":
        return _open_path(action.path)
    elif action.kind == "scroll":
        pyautogui.moveTo(action.x, action.y, duration=move_duration)
        if action.dx:
            pyautogui.hscroll(action.dx)
        if action.dy:
            pyautogui.vscroll(action.dy)
        return f"scrolled dx={action.dx} dy={action.dy} at ({action.x}, {action.y})"
    elif action.kind == "track_click":
        from . import reflex
        return reflex.track_click_target(action.x, action.y, timeout_s=action.seconds)
    elif action.kind == "wait":
        seconds = action.seconds or 1.0
        time.sleep(seconds)
        return f"waited {seconds}s"
    elif action.kind == "screenshot":
        return "no-op: a fresh screenshot is captured automatically every step"
    else:
        raise ValueError(f"execute() called with a non-executable action kind: {action.kind!r}")


def _paste_text(text: str) -> None:
    import pyautogui
    try:
        import pyperclip
    except Exception as e:
        raise RuntimeError(_CLIPBOARD_HELP.format(error=e)) from e
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None
    try:
        pyperclip.copy(text)
    except Exception as e:
        raise RuntimeError(_CLIPBOARD_HELP.format(error=e)) from e
    modifier = "command" if sys.platform == "darwin" else "ctrl"
    try:
        pyautogui.hotkey(modifier, "v")
        time.sleep(CLIPBOARD_SETTLE)
    finally:
        try:
            pyperclip.copy(previous if previous is not None else "")
        except Exception:
            pass


def _open_path(path: str | None) -> str:
    if not path:
        raise ValueError("open action requires a 'path'")
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
    return f"opened {path} with the default handler"
