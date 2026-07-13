"""On-machine seam: read the live desktop accessibility tree into AxElements.

axtree.py is the tested brain (find the element under a point, re-find by
identity). This is the thin, OS-specific glue that can only run on a real
desktop: it walks the platform's accessibility API -- UI Automation on Windows,
AT-SPI on Linux, the AX API on macOS -- and flattens the foreground window into
`axtree.AxElement`s. There is no display or accessibility bus in CI, so this
half is verified on your machine, exactly like the pyautogui input path.

A DesktopAxProvider is optional. Without one (the default), DesktopBackend isn't
element-aware and macro replay uses the visual anchor / coordinate tiers. Turn
it on with `secdogie-agent --desktop-ax`, which builds the provider for your
platform via `make_desktop_ax_provider`; if the platform library isn't
installed, that logs a one-line hint and returns None, so nothing breaks -- you
just don't get the semantic tier.
"""
from __future__ import annotations

import sys
from typing import Protocol, runtime_checkable

from . import axtree

# A live tree can be large; cap the walk so a pathological app can't hang replay.
MAX_TREE_DEPTH = 40


@runtime_checkable
class DesktopAxProvider(Protocol):
    def snapshot(self) -> list[axtree.AxElement] | None:
        """The foreground window's accessibility elements right now, or None if
        the tree can't be read. Called once per describe/locate -- not a hot
        path, so a fresh read each time is fine and always current."""
        ...


def make_desktop_ax_provider(logger=None) -> DesktopAxProvider | None:
    """Build the accessibility provider for this platform, or None (with a hint)
    if its library isn't available. Never raises -- a missing provider just means
    the semantic tier is off."""
    if sys.platform.startswith("win"):
        return _make_windows_provider(logger)
    hint = {
        "linux": "Linux AT-SPI support isn't wired yet -- implement a DesktopAxProvider "
                 "over pyatspi against the axtree.AxElement contract (see desktop_ax.py).",
        "darwin": "macOS AX API support isn't wired yet -- implement a DesktopAxProvider "
                  "over pyobjc's ApplicationServices against the axtree.AxElement contract.",
    }.get("darwin" if sys.platform == "darwin" else "linux")
    if logger is not None:
        logger.info("desktop accessibility: %s", hint)
    return None


def _make_windows_provider(logger) -> DesktopAxProvider | None:
    try:
        import uiautomation  # noqa: F401  (probe: is the library present?)
    except Exception as e:
        if logger is not None:
            logger.info(
                "desktop accessibility: the `uiautomation` package isn't available (%s); "
                "install it with `pip install uiautomation` to enable the semantic tier on Windows",
                e,
            )
        return None
    return _WindowsUiaProvider()


class _WindowsUiaProvider:
    """Reads the Windows UI Automation tree via the `uiautomation` package.

    On-machine only. The mapping from a UIA Control to an AxElement is isolated
    in `_element_of` so it's the single place to adjust if a property name
    differs in your `uiautomation` version; everything downstream is the tested
    axtree logic. Property/method names here follow the `uiautomation` package's
    documented API (Control.Name/AutomationId/ControlTypeName/BoundingRectangle/
    GetChildren, and GetForegroundControl)."""

    def snapshot(self) -> list[axtree.AxElement] | None:
        import uiautomation as auto

        root = auto.GetForegroundControl()  # the active window; bounds the walk
        if root is None:
            return None
        out: list[axtree.AxElement] = []
        self._walk(root, 0, out)
        return out

    def _walk(self, control, depth: int, out: list[axtree.AxElement]) -> None:
        el = self._element_of(control)
        if el is not None:
            out.append(el)
        if depth >= MAX_TREE_DEPTH:
            return
        try:
            children = control.GetChildren()
        except Exception:
            return  # a control can vanish mid-walk; skip its subtree rather than fail the snapshot
        for child in children:
            self._walk(child, depth + 1, out)

    @staticmethod
    def _element_of(control) -> axtree.AxElement | None:
        """Map one UIA Control to an AxElement, or None if it has no usable box.
        Best-effort: any attribute miss drops the element (the walk continues)."""
        try:
            rect = control.BoundingRectangle
            left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
            if right <= left or bottom <= top:
                return None  # zero-area / offscreen controls aren't click targets
            role = control.ControlTypeName or ""
            # ControlTypeName is like "ButtonControl"; trim the "Control" suffix
            # so it reads as the plain role the selector stores ("Button").
            if role.endswith("Control"):
                role = role[: -len("Control")]
            return axtree.AxElement(
                role=role,
                name=control.Name or "",
                automation_id=control.AutomationId or "",
                bounds=(left, top, right, bottom),
            )
        except Exception:
            return None
