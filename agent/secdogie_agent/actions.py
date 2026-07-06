"""Executes a validated Action against the real mouse/keyboard via pyautogui.

`done` and `ask_user` are handled by the agent loop, not here -- they end
or pause the loop rather than performing an OS-level action.

Movement is deliberately not instantaneous: teleporting the cursor and
clicking in the same tick makes some apps miss hover/focus events. We move
over a short duration and pause briefly before clicking, which is both more
reliable and closer to human input.
"""
from __future__ import annotations

import time

from .providers.base import Action

# Seconds to glide the cursor to a target, and to hover before pressing.
DEFAULT_MOVE_DURATION = 0.15
DEFAULT_SETTLE = 0.05


def execute(
    action: Action,
    move_duration: float = DEFAULT_MOVE_DURATION,
    settle: float = DEFAULT_SETTLE,
) -> str:
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
        pyautogui.typewrite(text, interval=0.02)
        return f"typed {len(text)} character(s)"
    elif action.kind == "key":
        keys = action.keys or []
        if len(keys) == 1:
            pyautogui.press(keys[0])
        elif len(keys) > 1:
            pyautogui.hotkey(*keys)
        return f"pressed key(s): {keys}"
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
