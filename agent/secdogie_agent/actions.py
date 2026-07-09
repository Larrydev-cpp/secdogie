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

from .providers.base import Action

# Seconds to glide the cursor to a target, and to hover before pressing.
DEFAULT_MOVE_DURATION = 0.15
DEFAULT_SETTLE = 0.05

# There is exactly one physical cursor/keyboard, and a click is a non-atomic
# move -> settle -> press sequence. When several desktop actors run at once in
# one process (open/ drives one agent per window), two concurrent clicks would
# interleave -- one actor's move lands the shared cursor, another's move steals
# it, and the first actor's press fires at the wrong place. This lock serializes
# input-emitting actions so each move+press completes as a unit. Actions that
# touch no input (wait/screenshot/open) skip it, so a long wait in one actor
# doesn't stall another.
_INPUT_LOCK = threading.Lock()
_NON_INPUT_KINDS = frozenset({"wait", "screenshot", "open"})
_NULL_CTX = contextlib.nullcontext()


def execute(
    action: Action,
    move_duration: float = DEFAULT_MOVE_DURATION,
    settle: float = DEFAULT_SETTLE,
) -> str:
    """Execute one action, holding the shared input lock for anything that
    drives the real mouse/keyboard so concurrent desktop actors don't corrupt
    each other's cursor position."""
    guard = _NULL_CTX if action.kind in _NON_INPUT_KINDS else _INPUT_LOCK
    with guard:
        return _dispatch(action, move_duration, settle)


def _dispatch(action: Action, move_duration: float, settle: float) -> str:
    import pyautogui

    def _approach(x: int, y: int) -> None:
        """Glide to (x, y) and let the UI register the hover before we act."""
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
        # pyautogui.typewrite can only emit ASCII; route Unicode (Chinese,
        # emoji, accents, ...) through the clipboard so it types correctly.
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
                pyautogui.keyUp(k)  # always release, even if interrupted
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
    elif action.kind == "wait":
        seconds = action.seconds or 1.0
        time.sleep(seconds)
        return f"waited {seconds}s"
    elif action.kind == "screenshot":
        return "no-op: a fresh screenshot is captured automatically every step"
    else:
        raise ValueError(f"execute() called with a non-executable action kind: {action.kind!r}")


def _paste_text(text: str) -> None:
    """Type arbitrary Unicode by putting it on the clipboard and pasting."""
    import pyautogui

    try:
        import pyperclip

        pyperclip.copy(text)
    except Exception as e:
        raise RuntimeError(
            "typing non-ASCII text (e.g. Chinese) needs clipboard access. Install "
            "the pyperclip backend for your OS: on Linux `sudo apt install xclip` "
            "(or xsel); pyperclip is bundled and works out of the box on Windows/macOS. "
            f"underlying error: {e}"
        ) from e
    modifier = "command" if sys.platform == "darwin" else "ctrl"
    pyautogui.hotkey(modifier, "v")


def _open_path(path: str | None) -> str:
    """Open a file/URL with the OS default handler (no mouse needed)."""
    if not path:
        raise ValueError("open action requires a 'path'")
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]  # Windows-only
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
    return f"opened {path} with the default handler"
