"""On-machine seam: read the live desktop accessibility tree into AxElements.

axtree.py is the tested brain (find the element under a point, re-find by
identity). This is the thin, OS-specific glue that can only run on a real
desktop: it walks the platform's accessibility API -- UI Automation on Windows,
AT-SPI on Linux, the AX API on macOS (all three wired) -- and flattens the
foreground window into `axtree.AxElement`s. There is no display or accessibility
bus in CI, so this half is verified on your machine, exactly like the pyautogui
input path.

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
    if sys.platform.startswith("linux"):
        return _make_linux_provider(logger)
    if sys.platform == "darwin":
        return _make_macos_provider(logger)
    if logger is not None:
        logger.info("desktop accessibility: no provider for platform %r", sys.platform)
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


def _make_linux_provider(logger) -> DesktopAxProvider | None:
    try:
        import pyatspi  # noqa: F401  (probe: are the AT-SPI bindings present?)
    except Exception as e:
        if logger is not None:
            logger.info(
                "desktop accessibility: the `pyatspi` AT-SPI bindings aren't available (%s); "
                "install them (e.g. `apt install python3-pyatspi gir1.2-atspi-2.0`) and enable your "
                "desktop's accessibility bus to use the semantic tier on Linux",
                e,
            )
        return None
    return _AtspiProvider()


class _AtspiProvider:
    """Reads the Linux AT-SPI tree via the `pyatspi` bindings.

    On-machine only: needs a running desktop with the accessibility bus enabled.
    AT-SPI has no single "foreground control", so snapshot finds the active
    top-level window (the frame whose state set contains STATE_ACTIVE) and walks
    its subtree. The Accessible->AxElement mapping is isolated in `_element_of`;
    method names follow pyatspi's documented API (Registry.getDesktop,
    Accessible.getRoleName/name/getState/getChildCount/getChildAtIndex, and the
    Component interface's getExtents(DESKTOP_COORDS)). Unlike Windows UIA there is
    no universal automation-id, so elements anchor on name+role, which AT-SPI
    exposes reliably."""

    def snapshot(self) -> list[axtree.AxElement] | None:
        import pyatspi

        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except Exception:
            return None
        frame = self._active_frame(pyatspi, desktop)
        if frame is None:
            return None
        out: list[axtree.AxElement] = []
        self._walk(pyatspi, frame, 0, out)
        return out

    def _active_frame(self, pyatspi, desktop):
        """The focused top-level window: the first frame (across all running
        apps) whose state set reports STATE_ACTIVE. None if nothing is active."""
        for app in self._children(desktop):
            for win in self._children(app):
                try:
                    if win.getState().contains(pyatspi.STATE_ACTIVE):
                        return win
                except Exception:
                    continue
        return None

    @staticmethod
    def _children(node) -> list:
        try:
            return [node.getChildAtIndex(i) for i in range(node.getChildCount())]
        except Exception:
            return []  # an accessible can disappear mid-walk; treat as leaf

    def _walk(self, pyatspi, node, depth: int, out: list[axtree.AxElement]) -> None:
        el = self._element_of(pyatspi, node)
        if el is not None:
            out.append(el)
        if depth >= MAX_TREE_DEPTH:
            return
        for child in self._children(node):
            self._walk(pyatspi, child, depth + 1, out)

    @staticmethod
    def _element_of(pyatspi, node) -> axtree.AxElement | None:
        """Map one AT-SPI Accessible to an AxElement, or None if it has no
        on-screen box (pure containers don't implement the Component interface).
        Best-effort: any failure drops the element and the walk continues."""
        try:
            component = node.queryComponent()
        except Exception:
            return None
        try:
            ext = component.getExtents(pyatspi.DESKTOP_COORDS)  # screen coordinates
            if ext.width <= 0 or ext.height <= 0:
                return None
            return axtree.AxElement(
                role=node.getRoleName() or "",
                name=node.name or "",
                automation_id="",  # AT-SPI has no universal stable id; anchor on name+role
                bounds=(ext.x, ext.y, ext.x + ext.width, ext.y + ext.height),
            )
        except Exception:
            return None


def _make_macos_provider(logger) -> DesktopAxProvider | None:
    try:
        import ApplicationServices  # noqa: F401  (probe: is pyobjc's AX framework present?)
    except Exception as e:
        if logger is not None:
            logger.info(
                "desktop accessibility: pyobjc's ApplicationServices isn't available (%s); "
                "install it with `pip install pyobjc-framework-ApplicationServices` and grant the "
                "host app Accessibility permission (System Settings -> Privacy & Security -> "
                "Accessibility) to enable the semantic tier on macOS",
                e,
            )
        return None
    return _MacosAxProvider(ApplicationServices)


class _MacosAxProvider:
    """Reads the macOS Accessibility (AX) tree via pyobjc's ApplicationServices.

    On-machine only, and with a second gate the other platforms don't have: the
    AX API returns nothing unless the *host app* (Terminal, the .app bundle, your
    IDE) has been granted Accessibility permission in System Settings -> Privacy &
    Security -> Accessibility. Without it snapshot() comes back empty and the flag
    quietly degrades, same as a missing library.

    The AXUIElement -> AxElement mapping is isolated in `_element_of`, and every
    AX call is funnelled through `_attr` (attribute read) and `_geometry`
    (position/size unwrap) -- the single places to adjust for a pyobjc version.
    Roles arrive as "AXButton"/"AXTextField"; the "AX" prefix is trimmed so they
    read as the plain role the selector stores ("Button"), matching Windows.
    macOS exposes an optional developer-set AXIdentifier used as the
    automation-id when present, else elements anchor on title+role like AT-SPI.

    `ax` (the ApplicationServices module) is injected so the walk/mapping logic is
    provable against a fake -- only the real framework binding is machine-specific.
    """

    def __init__(self, ax):
        self._ax = ax

    def snapshot(self) -> list[axtree.AxElement] | None:
        ax = self._ax
        system = ax.AXUIElementCreateSystemWide()
        # The focused app, then its focused window -- the same "active window
        # only" scope the Windows/Linux providers use, so the walk stays bounded.
        app = self._attr(system, ax.kAXFocusedApplicationAttribute)
        if app is None:
            return None
        window = self._attr(app, ax.kAXFocusedWindowAttribute)
        if window is None:
            return None
        out: list[axtree.AxElement] = []
        self._walk(window, 0, out)
        return out

    def _walk(self, element, depth: int, out: list[axtree.AxElement]) -> None:
        el = self._element_of(element)
        if el is not None:
            out.append(el)
        if depth >= MAX_TREE_DEPTH:
            return
        for child in self._children(element):
            self._walk(child, depth + 1, out)

    def _children(self, element) -> list:
        kids = self._attr(element, self._ax.kAXChildrenAttribute)
        return list(kids) if kids else []  # a leaf has no AXChildren -> None -> []

    def _attr(self, element, attribute):
        """Read one AX attribute value, or None. pyobjc returns (AXError, value);
        a non-zero error (attribute absent/unreadable) or any exception -> None,
        so a control that vanishes mid-walk is skipped rather than failing the
        snapshot."""
        try:
            err, value = self._ax.AXUIElementCopyAttributeValue(element, attribute, None)
        except Exception:
            return None
        if err != 0:  # kAXErrorSuccess == 0
            return None
        return value

    def _element_of(self, element) -> axtree.AxElement | None:
        """Map one AXUIElement to an AxElement, or None if it has no role or no
        on-screen box. Best-effort: any attribute miss drops the element."""
        ax = self._ax
        role = self._attr(element, ax.kAXRoleAttribute)
        if not role:
            return None
        geom = self._geometry(element)
        if geom is None:
            return None
        left, top, right, bottom = geom
        if right <= left or bottom <= top:
            return None  # zero-area / offscreen elements aren't click targets
        role = str(role)
        if role.startswith("AX"):
            role = role[2:]  # "AXButton" -> "Button", matching the Windows convention
        name = self._attr(element, ax.kAXTitleAttribute) or self._attr(element, ax.kAXDescriptionAttribute) or ""
        automation_id = self._attr(element, ax.kAXIdentifierAttribute) or ""
        return axtree.AxElement(
            role=role,
            name=str(name),
            automation_id=str(automation_id),
            bounds=(left, top, right, bottom),
        )

    def _geometry(self, element) -> tuple[int, int, int, int] | None:
        """(left, top, right, bottom) in screen pixels from AXPosition + AXSize,
        or None if either is unreadable. Each attribute is an AXValue that must be
        unwrapped to a CGPoint/CGSize via AXValueGetValue. The CGPoint/CGSize type
        constants were renamed across pyobjc versions (kAXValueCGPointType ->
        kAXValueTypeCGPoint), so we accept either -- this is the one place a
        version difference would surface."""
        ax = self._ax
        pos = self._attr(element, ax.kAXPositionAttribute)
        size = self._attr(element, ax.kAXSizeAttribute)
        if pos is None or size is None:
            return None
        point_type = getattr(ax, "kAXValueCGPointType", None)
        if point_type is None:
            point_type = getattr(ax, "kAXValueTypeCGPoint", None)
        size_type = getattr(ax, "kAXValueCGSizeType", None)
        if size_type is None:
            size_type = getattr(ax, "kAXValueTypeCGSize", None)
        try:
            ok_p, point = ax.AXValueGetValue(pos, point_type, None)
            ok_s, dims = ax.AXValueGetValue(size, size_type, None)
        except Exception:
            return None
        if not ok_p or not ok_s:
            return None
        left, top = int(point.x), int(point.y)
        return (left, top, left + int(dims.width), top + int(dims.height))
