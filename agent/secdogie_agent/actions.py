"""Executes a validated Action against the real mouse/keyboard via pyautogui.

`done` and `ask_user` are handled by the agent loop, not here -- they end
or pause the loop rather than performing an OS-level action.
"""
from __future__ import annotations

import time

from .providers.base import Action


def execute(action: Action) -> str:
    import pyautogui

    if action.kind == "left_click":
        pyautogui.click(action.x, action.y, button="left")
        return f"clicked left at ({action.x}, {action.y})"
    elif action.kind == "right_click":
        pyautogui.click(action.x, action.y, button="right")
        return f"clicked right at ({action.x}, {action.y})"
    elif action.kind == "double_click":
        pyautogui.doubleClick(action.x, action.y)
        return f"double-clicked at ({action.x}, {action.y})"
    elif action.kind == "move":
        pyautogui.moveTo(action.x, action.y)
        return f"moved cursor to ({action.x}, {action.y})"
    elif action.kind == "drag":
        pyautogui.moveTo(action.x, action.y)
        pyautogui.dragTo(action.to_x, action.to_y, button="left")
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
        pyautogui.moveTo(action.x, action.y)
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
