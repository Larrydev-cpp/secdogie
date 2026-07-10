"""An agent Backend that drives an iPhone/iPad through WebDriverAgent.

Slots into secdogie_agent's loop in place of the desktop backend, the same way
the Android adb backend does. The one iOS-specific wrinkle is coordinates:
WDA's screenshot is in device *pixels* but its tap/drag API wants *points*
(the Retina scale factor, 2x or 3x). The loop hands `execute` coordinates in
pixel space (it scaled them off the pixel screenshot), so this backend divides
by the pixel/point ratio -- captured each frame in `capture` -- before calling
WDA.
"""
from __future__ import annotations

import io
import random

from secdogie_agent import screen
from secdogie_agent.providers.base import Action

from .wda import Wda, WdaError

# scroll carries a direction but no distance; turn each into a swipe of this
# many *points* in the indicated direction.
_SCROLL_SWIPE_PT = 300
_LONG_PRESS_SECONDS = 0.6
_DRAG_SECONDS = 0.4

# WDA's /wda/tap has no duration knob -- a real finger's contact time varies,
# this endpoint's doesn't. When humanize_taps is on, a tap is issued instead as
# touchAndHold with a short randomized duration (the same primitive right_click
# already uses for a real long-press, just much shorter). Range is typical
# human tap dwell time, not tuned against any specific detector. NOTE:
# touchAndHold maps to XCUITest's press(forDuration:), a different underlying
# gesture from tap() -- see ios/README.md for what this does and does not change.
_HUMANIZE_DURATION_S = (0.05, 0.14)

# Model key names -> WDA hardware buttons (the only physical buttons WDA can
# press). Everything else is a keyboard key and goes through typed text.
_HARDWARE_BUTTONS = {
    "home": "home",
    "volup": "volumeUp",
    "volumeup": "volumeUp",
    "voldown": "volumeDown",
    "volumedown": "volumeDown",
}
# Model key names -> the character WDA should type for them.
_TYPED_KEYS = {
    "enter": "\n",
    "return": "\n",
    "tab": "\t",
    "space": " ",
    "backspace": "\b",
    "delete": "\b",
}


class IosBackend:
    def __init__(self, wda: Wda, humanize_taps: bool = False, rng: random.Random | None = None):
        self.wda = wda
        # pixels-per-point for the current frame; set in capture(), used to map
        # the loop's pixel coordinates down to WDA's point coordinates.
        self._px_per_pt = 1.0
        # See _HUMANIZE_DURATION_S above.
        self.humanize_taps = humanize_taps
        self._rng = rng if rng is not None else random.Random()

    def _tap(self, x: int, y: int) -> None:
        """A single tap, humanized if enabled."""
        if not self.humanize_taps:
            self.wda.tap(x, y)
            return
        duration = self._rng.uniform(*_HUMANIZE_DURATION_S)
        self.wda.touch_and_hold(x, y, duration)

    def setup(self, logger) -> None:
        # Don't hard-fail -- --dry-run should work with WDA down. A real
        # capture/action will surface an actionable WdaError.
        try:
            self.wda.status()
        except WdaError as e:
            logger.warning("WebDriverAgent not reachable yet (%s); only --dry-run will work", e)

    def capture(self, region: tuple[int, int, int, int] | None):
        # iOS has no region concept -- always the whole device screen.
        try:
            png = self.wda.screenshot_png()
            point_w, _point_h = self.wda.window_size()
        except WdaError as e:
            raise screen.CaptureError(str(e)) from e

        from PIL import Image

        with Image.open(io.BytesIO(png)) as img:
            pixel_w, pixel_h = img.width, img.height
        # Retina factor: screenshot pixels are point_w-wide in points, so this
        # is how many pixels make up one point. Guard a zero width defensively.
        self._px_per_pt = (pixel_w / point_w) if point_w else 1.0
        return png, (pixel_w, pixel_h)

    def _pt(self, pixel_value: int | None) -> int:
        return int(round((pixel_value or 0) / self._px_per_pt))

    def execute(self, action: Action) -> str:
        a = action
        if a.kind == "left_click":
            x, y = self._pt(a.x), self._pt(a.y)
            self._tap(x, y)
            return f"tapped ({x}, {y}) pt"
        if a.kind == "double_click":
            # Always WDA's real doubleTap gesture, even when humanizing: unlike
            # Android (where a double-tap is just two ordinary tap events an app
            # times itself), WDA's doubleTap is its own distinct gesture, and
            # substituting two touchAndHolds would risk not registering as a
            # double-tap at all rather than just changing its timing signature.
            x, y = self._pt(a.x), self._pt(a.y)
            self.wda.double_tap(x, y)
            return f"double-tapped ({x}, {y}) pt"
        if a.kind == "right_click":
            # No right-click on touch; press-and-hold is the context gesture.
            x, y = self._pt(a.x), self._pt(a.y)
            self.wda.touch_and_hold(x, y, _LONG_PRESS_SECONDS)
            return f"long-pressed ({x}, {y}) pt"
        if a.kind == "move":
            # A touchscreen has no hover cursor; moving without touching is a no-op.
            return "no-op: a touchscreen has no cursor to move without tapping"
        if a.kind == "drag":
            fx, fy, tx, ty = self._pt(a.x), self._pt(a.y), self._pt(a.to_x), self._pt(a.to_y)
            self.wda.drag(fx, fy, tx, ty, _DRAG_SECONDS)
            return f"dragged ({fx}, {fy}) -> ({tx}, {ty}) pt"
        if a.kind == "type":
            text = a.text or ""
            # WDA types into the focused field and, unlike adb, handles Unicode.
            self.wda.type_text(text)
            return f"typed {len(text)} character(s)"
        if a.kind == "key":
            return self._keys(a.keys or [])
        if a.kind == "hold_key":
            # WDA has no timed key-hold; press each key once. Hardware buttons
            # get pressed, keyboard keys get typed. The requested duration can't
            # be honored.
            return self._keys(a.keys or [], note="(hold duration not supported on iOS)")
        if a.kind == "scroll":
            return self._scroll(a)
        if a.kind == "open":
            if not a.path:
                raise ValueError("open action requires a 'path'")
            self.wda.open_url(a.path)
            return f"opened {a.path}"
        raise ValueError(f"IosBackend can't execute action kind: {a.kind!r}")

    def _keys(self, keys: list[str], note: str = "") -> str:
        for k in keys:
            low = k.strip().lower()
            if low in _HARDWARE_BUTTONS:
                self.wda.press_button(_HARDWARE_BUTTONS[low])
            elif low in _TYPED_KEYS:
                self.wda.type_text(_TYPED_KEYS[low])
            else:
                # A single character or an unmapped key name -> type it verbatim.
                self.wda.type_text(k)
        return f"sent key(s): {keys}{(' ' + note) if note else ''}"

    def _scroll(self, a: Action) -> str:
        x, y = self._pt(a.x), self._pt(a.y)
        dx, dy = a.dx or 0, a.dy or 0
        # Content scroll -> finger swipe (drag) in the opposite direction.
        # Positive dy (reveal content below) swipes upward; clamp to the edge.
        if dy:
            end_y = max(0, y - _SCROLL_SWIPE_PT) if dy > 0 else y + _SCROLL_SWIPE_PT
            self.wda.drag(x, y, x, end_y, _DRAG_SECONDS)
        if dx:
            end_x = max(0, x - _SCROLL_SWIPE_PT) if dx > 0 else x + _SCROLL_SWIPE_PT
            self.wda.drag(x, y, end_x, y, _DRAG_SECONDS)
        if not dx and not dy:
            return "no-op: scroll with no dx/dy"
        return f"scrolled via swipe at ({x}, {y}) pt dx={dx} dy={dy}"
