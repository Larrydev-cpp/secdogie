"""An agent Backend that drives an Android device over adb.

Slots into secdogie_agent's loop in place of the desktop backend: same
screenshot -> model -> one action -> repeat loop, same Action schema, just
translated to `adb` calls. The loop has already mapped model coordinates to
real device pixels before `execute` sees them (screencap returns the device's
true resolution, which the loop uses to scale), so taps land where intended.
"""
from __future__ import annotations

import io

from secdogie_agent import screen
from secdogie_agent.providers.base import Action

from . import uitree
from .adb import Adb, AdbError
from .uitree import UiElement

# A scroll action carries a direction (sign of dx/dy) but not a pixel distance;
# on a touchscreen we turn each scroll into a swipe of this many pixels.
_SCROLL_SWIPE_PX = 600
_DRAG_DURATION_MS = 400


class AdbBackend:
    def __init__(self, adb: Adb, snap_to_elements: bool = False, max_snap_area_frac: float = 0.25):
        self.adb = adb
        # RPA-style targeting: when on, a tap is snapped onto the real UI widget
        # under the model's point (read from the uiautomator view hierarchy)
        # instead of using the raw pixel coordinate -- but only if that widget is
        # control-sized (its box is at most max_snap_area_frac of the screen), so
        # we don't snap onto a big backdrop/container the point merely falls in.
        self.snap_to_elements = snap_to_elements
        self.max_snap_area_frac = max_snap_area_frac
        self._screen_px: tuple[int, int] | None = None  # last captured screen size, for the area guard

    def setup(self, logger) -> None:
        # Don't hard-fail here -- --dry-run should work with no device attached.
        # A real capture/action later will surface an actionable AdbError.
        try:
            devices = self.adb.list_devices()
        except AdbError as e:
            logger.warning("could not list adb devices (%s); only --dry-run will work", e)
            return
        if not devices:
            logger.warning("no adb device is connected/authorized; only --dry-run will work")
        elif self.adb.serial is None and len(devices) > 1:
            logger.warning(
                "%d devices attached (%s); pass --device <serial> or the first real action will fail",
                len(devices),
                ", ".join(devices),
            )

    def capture(self, region: tuple[int, int, int, int] | None):
        # Android backend has no region concept -- it always captures the whole
        # device screen. (region is a desktop multi-window notion.)
        try:
            png = self.adb.screencap_png()
        except AdbError as e:
            # Re-raise as the loop's capture error so it exits cleanly (code 4)
            # instead of crashing on an adb failure.
            raise screen.CaptureError(str(e)) from e
        from PIL import Image

        with Image.open(io.BytesIO(png)) as img:
            size = (img.width, img.height)
        self._screen_px = size
        return png, size

    def find_element(
        self,
        *,
        text: str | None = None,
        resource_id: str | None = None,
        content_desc: str | None = None,
        clickable_only: bool = False,
    ) -> UiElement | None:
        """First UI widget on the current screen matching the selector, read
        from the uiautomator hierarchy. Returns None if nothing matches or the
        screen can't be dumped."""
        try:
            elements = uitree.parse(self.adb.ui_dump())
        except (AdbError, ValueError):
            return None
        matches = uitree.find_elements(
            elements,
            text=text,
            resource_id=resource_id,
            content_desc=content_desc,
            clickable_only=clickable_only,
        )
        return matches[0] if matches else None

    def _snap(self, x: int, y: int) -> tuple[int, int, str]:
        """Snap (x, y) onto the widget under it, if snapping is on and a nearby
        clickable element is found. Best-effort: any failure keeps the raw
        coordinate. Returns (x, y, note)."""
        if not self.snap_to_elements:
            return x, y, ""
        try:
            elements = uitree.parse(self.adb.ui_dump())
        except (AdbError, ValueError):
            return x, y, ""
        el = uitree.smallest_clickable_at(elements, x, y)
        if el is None:
            return x, y, ""
        if self._screen_px is not None:
            screen_area = self._screen_px[0] * self._screen_px[1]
            if screen_area > 0 and el.area > self.max_snap_area_frac * screen_area:
                return x, y, ""  # a big container, not a specific control -- don't move the tap
        cx, cy = el.center
        label = el.text or el.content_desc or el.resource_id.rsplit("/", 1)[-1] or el.cls
        return cx, cy, f" (snapped to '{label}')" if label else " (snapped to element)"

    def execute(self, action: Action) -> str:
        a = action
        if a.kind == "left_click":
            x, y, note = self._snap(a.x, a.y)
            self.adb.tap(x, y)
            return f"tapped ({x}, {y}){note}"
        if a.kind == "double_click":
            x, y, note = self._snap(a.x, a.y)
            self.adb.tap(x, y)
            self.adb.tap(x, y)
            return f"double-tapped ({x}, {y}){note}"
        if a.kind == "right_click":
            # No right-click on touch; long-press is the context-menu gesture.
            x, y, note = self._snap(a.x, a.y)
            self.adb.long_press(x, y)
            return f"long-pressed ({x}, {y}){note}"
        if a.kind == "move":
            # A touchscreen has no hover cursor, so moving without touching is a
            # no-op -- report it so the model doesn't think the step was lost.
            return "no-op: a touchscreen has no cursor to move without tapping"
        if a.kind == "drag":
            self.adb.swipe(a.x, a.y, a.to_x, a.to_y, _DRAG_DURATION_MS)
            return f"dragged ({a.x}, {a.y}) -> ({a.to_x}, {a.to_y})"
        if a.kind == "type":
            text = a.text or ""
            if not text.isascii():
                # `adb shell input text` is ASCII-only; typing Unicode needs an
                # on-device IME like ADBKeyBoard, which is out of scope here.
                return (
                    f"skipped: cannot type non-ASCII text ({len(text)} chars) over adb; "
                    "install an IME such as ADBKeyBoard to enter Unicode"
                )
            self.adb.text(text)
            return f"typed {len(text)} character(s)"
        if a.kind == "key":
            keys = a.keys or []
            for k in keys:
                self.adb.keyevent(k)
            return f"sent key event(s): {keys}"
        if a.kind == "hold_key":
            keys = a.keys or []
            for k in keys:
                self.adb.keyevent(k, longpress=True)
            # adb's --longpress is a single fixed-duration hold; the requested
            # seconds can't be honored precisely.
            return f"long-pressed key(s): {keys}"
        if a.kind == "scroll":
            return self._scroll(a)
        if a.kind == "open":
            if not a.path:
                raise ValueError("open action requires a 'path'")
            self.adb.open_uri(a.path)
            return f"opened {a.path} via VIEW intent"
        raise ValueError(f"AdbBackend can't execute action kind: {a.kind!r}")

    def _scroll(self, a: Action) -> str:
        x, y = a.x or 0, a.y or 0
        dx, dy = a.dx or 0, a.dy or 0
        # Content scroll -> finger swipe in the opposite direction. Positive dy
        # (scroll down to reveal content below) is a swipe upward, etc. Clamp
        # the endpoint to the top/left edge so we never swipe to a negative px.
        if dy:
            end_y = max(0, y - _SCROLL_SWIPE_PX) if dy > 0 else y + _SCROLL_SWIPE_PX
            self.adb.swipe(x, y, x, end_y)
        if dx:
            end_x = max(0, x - _SCROLL_SWIPE_PX) if dx > 0 else x + _SCROLL_SWIPE_PX
            self.adb.swipe(x, y, end_x, y)
        if not dx and not dy:
            return "no-op: scroll with no dx/dy"
        return f"scrolled via swipe at ({x}, {y}) dx={dx} dy={dy}"
