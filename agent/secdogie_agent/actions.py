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

# Seconds to wait after Ctrl+V before restoring the user's clipboard, so the
# target app has actually read our text out of it first (see _paste_text).
CLIPBOARD_SETTLE = 0.15

_CLIPBOARD_HELP = (
    "typing non-ASCII text (e.g. Chinese) needs clipboard access. Install "
    "the pyperclip backend for your OS: on Linux `sudo apt install xclip` "
    "(or xsel); pyperclip is bundled and works out of the box on Windows/macOS. "
    "underlying error: {error}"
)

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

# Appended to an action's result when the pre-action focus hook could not
# confirm the target window was frontmost. The action still runs (see execute),
# but a click that may have landed on whatever window WAS in front must not be
# reported as a plain success: the note rides the result string back into the
# log, the model's history, and the audit trace. loop.py matches on this exact
# constant to re-log it at WARNING -- import it, don't retype the text.
FOCUS_UNCONFIRMED_NOTE = " [focus unconfirmed: the target window may not have been frontmost]"

# Action kinds that reach OUTSIDE the "move the mouse / type into the focused
# window" sandbox and can have consequences a screenshot can't undo. `open`
# hands a path/URL to the OS default handler (_open_path below); `run_elevated`
# runs a command as SYSTEM (handled in loop.py, gated by an operator allowlist,
# never dispatched here) -- both can launch a program or an installer, unlike
# every other kind, which only manipulates whatever window already has focus.
# The loop force-confirms these even under --auto (see loop.confirm_high_risk);
# keep the set tight -- a kind belongs here only if it can act beyond the screen.
HIGH_RISK_KINDS = frozenset({"open", "run_elevated"})


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
            return True  # Ctrl/Cmd+S → save
        if keys_lower & {"delete", "backspace"}:
            return True
        if "f4" in keys_lower and bool(keys_lower & {"alt", "option"}):
            return True  # Alt+F4 → close
        if "w" in keys_lower and bool(keys_lower & {"ctrl", "control", "command", "cmd"}):
            return True  # Ctrl/Cmd+W → close tab/window
    return False


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
    activation does not block the action (best-effort) -- the action still runs
    rather than silently doing nothing -- but it is no longer swallowed either:
    the result comes back carrying FOCUS_UNCONFIRMED_NOTE so the caller, the
    model, and the trace all learn the click may have gone somewhere else."""
    guard = _NULL_CTX if action.kind in _NON_INPUT_KINDS else _INPUT_LOCK
    with guard:
        confirmed = True
        if activate is not None and action.kind not in _NON_INPUT_KINDS:
            confirmed = _activated(activate)
        result = _dispatch(action, move_duration, settle)
        return result if confirmed else result + FOCUS_UNCONFIRMED_NOTE


def _activated(activate: Callable[[], bool]) -> bool:
    """Run the focus hook and report whether it confirmed focus. A hook that
    returns None isn't claiming anything (plenty of them just raise a window
    and don't check), so only an explicit False -- or an exception -- counts as
    "not confirmed"."""
    try:
        return activate() is not False
    except Exception:
        return False


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
    """Type arbitrary Unicode by putting it on the clipboard and pasting.

    The clipboard belongs to the user, not to us: whatever they had copied is
    read back first and put there again afterwards, so automating one line of
    Chinese doesn't quietly destroy something they were in the middle of
    pasting themselves.

    Two honest limits. The restore waits CLIPBOARD_SETTLE seconds first,
    because Ctrl+V is asynchronous -- the target app reads the clipboard while
    handling the keystroke, and putting the old contents back too early makes
    it paste the wrong thing. And a clipboard holding something that isn't text
    (an image, a file list) can't be read back through pyperclip, so there is
    nothing to restore; we clear it rather than leave our text sitting there
    masquerading as theirs.
    """
    import pyautogui

    try:
        import pyperclip
    except Exception as e:
        raise RuntimeError(_CLIPBOARD_HELP.format(error=e)) from e

    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None  # non-text or unreadable clipboard: nothing to put back

    try:
        pyperclip.copy(text)
    except Exception as e:
        # Raised here rather than on import: on Linux pyperclip imports fine
        # and only fails once it looks for xclip/xsel to actually copy.
        raise RuntimeError(_CLIPBOARD_HELP.format(error=e)) from e

    modifier = "command" if sys.platform == "darwin" else "ctrl"
    try:
        pyautogui.hotkey(modifier, "v")
        time.sleep(CLIPBOARD_SETTLE)
    finally:
        try:
            pyperclip.copy(previous if previous is not None else "")
        except Exception:
            pass  # best-effort: failing to restore must not fail the typing itself


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
