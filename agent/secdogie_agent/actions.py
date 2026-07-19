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

# Action kinds that reach OUTSIDE the "move the mouse / type into the focused
# window" sandbox and can have consequences a screenshot can't undo. `open`
# hands an arbitrary path/URL to the OS default handler (_open_path below), so
# it can launch a program, run an installer, or open a link -- unlike every
# other kind, which only manipulates whatever window already has focus. The
# loop force-confirms these even under --auto (see loop.confirm_high_risk); keep
# the set tight -- a kind belongs here only if it can act beyond the screen.
HIGH_RISK_KINDS = frozenset({"open"})


def execute(
    action: Action,
    move_duration: float = DEFAULT_MOVE_DURATION,
    settle: float = DEFAULT_SETTLE,
    activate: Callable[[], bool] | None = None,
) -> str:
    """Execute one action, holding the shared input lock for anything that
    drives the real mouse/keyboard so concurrent desktop actors don't corrupt
    each other's cursor position.

    `activate`, if given, is called INSIDE the lock, before the action itself,
    to bring this actor's target window to the foreground and confirm it took
    focus (see open/secdogie_open/windows.py's focus_window, used as this hook
    by runner.py). Doing that inside the same locked section as the action is
    what makes "one actor's click+type, then the next actor's click+type"
    actually hold: since only one window can be the OS foreground window at a
    time, a later actor's activate() call cannot even begin -- it's waiting on
    this same lock -- until this action (and its own activate) has completed,
    so the earlier actor's window is guaranteed to have already lost focus by
    the time the next one runs. No separate "confirm focus released" check is
    needed; gaining focus for the next window IS that confirmation. A failed
    activation is swallowed (best-effort) -- the action still runs rather than
    silently doing nothing."""
    guard = _NULL_CTX if action.kind in _NON_INPUT_KINDS else _INPUT_LOCK
    with guard:
        if activate is not None and action.kind not in _NON_INPUT_KINDS:
            try:
                activate()
            except Exception:
                pass  # best-effort: the action still executes even if activation failed
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
    elif action.kind == "track_click":
        # Hand a MOVING target to the local reflex loop: track it at frame rate
        # and click it the moment it settles, with no model call per frame. This
        # runs inside the shared input lock (track_click is not benign), so the
        # whole multi-second pursuit owns the physical cursor exclusively -- no
        # other desktop actor can inject input mid-chase. numpy-gated; the reflex
        # layer raises a clear install hint if it's missing.
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
