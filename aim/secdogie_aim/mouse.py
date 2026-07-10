"""Relative mouse-look injection -- the input path a captured-pointer 3D game
needs, which absolute positioning (pyautogui.moveTo) cannot provide.

secdogie's desktop control moves the cursor to an ABSOLUTE screen coordinate.
That is exactly right for 2D UI ("click the button at (x, y)"), and useless for
a game like Minecraft: it grabs the pointer, hides it, and rotates the camera
from RELATIVE motion deltas. There is no on-screen cursor to position; the game
reads raw dx/dy. So camera aim needs a different OS call:

  - Windows: SendInput with MOUSEEVENTF_MOVE and NO absolute flag (the counts
    are relative "mickeys").
  - Linux:   a uinput virtual device emitting REL_X / REL_Y.

`RelativeMouse` is the small protocol both the aim controller and tests speak;
`RecordingMouse` is the headless fake the pure control-law tests drive (the real
injectors can only be verified on a machine with a display + the game, which is
called out in the README).
"""
from __future__ import annotations

import sys
from typing import Protocol, runtime_checkable


@runtime_checkable
class RelativeMouse(Protocol):
    """Move the view by a relative delta and fire. `move` is the mouse-look
    primitive absolute positioning can't express; `press`/`release` hold the
    attack button (melee = repeated click, bow = hold-then-release)."""

    def move(self, dx: int, dy: int) -> None: ...
    def press(self) -> None: ...
    def release(self) -> None: ...
    def click(self) -> None: ...


class RecordingMouse:
    """A RelativeMouse that records calls instead of touching any real device,
    so the aim control law can be tested headless. `moves` accumulates every
    (dx, dy); `net` is their sum (where the view ended up); `events` logs
    press/release/click order."""

    def __init__(self) -> None:
        self.moves: list[tuple[int, int]] = []
        self.events: list[str] = []

    def move(self, dx: int, dy: int) -> None:
        self.moves.append((dx, dy))

    def press(self) -> None:
        self.events.append("press")

    def release(self) -> None:
        self.events.append("release")

    def click(self) -> None:
        self.events.append("click")

    @property
    def net(self) -> tuple[int, int]:
        return (sum(dx for dx, _ in self.moves), sum(dy for _, dy in self.moves))


def _load_win_sendinput():
    """Build the SendInput ctypes machinery lazily. Kept out of module scope so
    importing this file on Linux (where ctypes.wintypes is unavailable) never
    fails -- only constructing WindowsMouse, on Windows, touches it."""
    import ctypes
    from ctypes import wintypes

    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    INPUT_MOUSE = 0

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("mi", _MOUSEINPUT)]

        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    send = ctypes.windll.user32.SendInput

    def _emit(flags: int, dx: int = 0, dy: int = 0) -> None:
        mi = _MOUSEINPUT(dx, dy, 0, flags, 0, None)
        inp = _INPUT(INPUT_MOUSE, _INPUT._U(mi))
        send(1, ctypes.byref(inp), ctypes.sizeof(inp))

    return _emit, MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP


class WindowsMouse:
    """Relative mouse-look + attack via Win32 SendInput. Verify on the machine:
    this environment has no display, so the plumbing is exercised only by the
    RecordingMouse-backed control-law tests."""

    def __init__(self) -> None:
        if not sys.platform.startswith("win"):
            raise RuntimeError("WindowsMouse is Windows-only; use open_mouse() to pick the right injector")
        self._emit, self._MOVE, self._DOWN, self._UP = _load_win_sendinput()

    def move(self, dx: int, dy: int) -> None:
        self._emit(self._MOVE, dx, dy)  # relative: no MOUSEEVENTF_ABSOLUTE

    def press(self) -> None:
        self._emit(self._DOWN)

    def release(self) -> None:
        self._emit(self._UP)

    def click(self) -> None:
        self.press()
        self.release()


class LinuxUinputMouse:
    """Relative mouse-look + attack via a uinput virtual device (REL_X/REL_Y).
    Needs the `python-uinput` package and write access to /dev/uinput (usually
    root or the `input` group)."""

    def __init__(self) -> None:
        try:
            import uinput
        except ImportError as e:
            raise RuntimeError(
                "Linux relative mouse-look needs python-uinput. Install it with "
                "`pip install 'secdogie-aim[linux]'` (and ensure /dev/uinput is writable)."
            ) from e
        self._uinput = uinput
        self._device = uinput.Device([uinput.REL_X, uinput.REL_Y, uinput.BTN_LEFT])

    def move(self, dx: int, dy: int) -> None:
        if dx:
            self._device.emit(self._uinput.REL_X, dx, syn=False)
        self._device.emit(self._uinput.REL_Y, dy)

    def press(self) -> None:
        self._device.emit(self._uinput.BTN_LEFT, 1)

    def release(self) -> None:
        self._device.emit(self._uinput.BTN_LEFT, 0)

    def click(self) -> None:
        self.press()
        self.release()


def open_mouse() -> RelativeMouse:
    """Pick the right relative-mouse injector for this OS."""
    if sys.platform.startswith("win"):
        return WindowsMouse()
    if sys.platform.startswith("linux"):
        return LinuxUinputMouse()
    raise RuntimeError("relative mouse-look injection is implemented for Windows and Linux only")
