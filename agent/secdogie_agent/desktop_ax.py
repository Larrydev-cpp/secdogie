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

`snapshot` is the required method (the listing / macro-replay half). Providers
also expose optional `press` / `set_value`: native Invoke/AXPress/AT-SPI click
and SetValue, so the live loop can drive a listed widget without moving the
real cursor. A provider that only implements snapshot still works -- the loop
falls back to a pixel click at the element's centre.

"""
from __future__ import annotations

import dataclasses
import sys
from typing import Protocol, runtime_checkable

from . import axtree, touch_probe

# A live tree can be large; cap the walk so a pathological app can't hang replay.
MAX_TREE_DEPTH = 40


def _match_kwargs(
    automation_id: str | None = None,
    name: str | None = None,
    role: str | None = None,
) -> dict[str, str]:
    """Kwargs for axtree.find_elements, dropping blanks so an omitted field
    does not force an exact match on "". """
    out: dict[str, str] = {}
    if automation_id:
        out["automation_id"] = automation_id
    if name:
        out["name"] = name
    if role:
        out["role"] = role
    return out


def _matches(el: axtree.AxElement | None, **attrs: str) -> bool:
    return bool(el is not None and attrs and axtree.find_elements([el], **attrs))



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
            out.append(dataclasses.replace(el, depth=depth))
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

    def press(self, automation_id: str | None = None, name: str | None = None, role: str | None = None) -> bool:
        """Invoke/Toggle the matching UIA control without moving the cursor."""
        control = self._find(automation_id=automation_id, name=name, role=role)
        if control is None:
            return False
        for method_name in ("Invoke", "Toggle"):
            fn = getattr(control, method_name, None)
            if not callable(fn):
                continue
            try:
                if fn() is False:
                    continue
                return True
            except Exception:
                continue
        return False

    def set_value(
        self,
        text: str,
        automation_id: str | None = None,
        name: str | None = None,
        role: str | None = None,
    ) -> bool:
        """Set a UIA ValuePattern without synthesizing keystrokes."""
        control = self._find(automation_id=automation_id, name=name, role=role)
        if control is None:
            return False
        try:
            pattern = control.GetValuePattern()
            if pattern is None:
                return False
            pattern.SetValue(text)
            return True
        except Exception:
            return False

    def _find(self, automation_id: str | None = None, name: str | None = None, role: str | None = None):
        import uiautomation as auto

        root = auto.GetForegroundControl()
        if root is None:
            return None
        attrs = _match_kwargs(automation_id, name, role)
        if not attrs:
            return None
        hits: list = []
        self._find_walk(root, 0, attrs, hits)
        return hits[0] if hits else None

    def _find_walk(self, control, depth: int, attrs: dict[str, str], hits: list) -> None:
        if _matches(self._element_of(control), **attrs):
            hits.append(control)
            return
        if depth >= MAX_TREE_DEPTH:
            return
        try:
            children = control.GetChildren()
        except Exception:
            return
        for child in children:
            if hits:
                return
            self._find_walk(child, depth + 1, attrs, hits)


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
            out.append(dataclasses.replace(el, depth=depth))
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

    def press(self, automation_id: str | None = None, name: str | None = None, role: str | None = None) -> bool:
        """doAction('click'/'press'/first action) on the matching AT-SPI node."""
        node = self._find(automation_id=automation_id, name=name, role=role)
        if node is None:
            return False
        try:
            action = node.queryAction()
        except Exception:
            return False
        try:
            n = int(action.nActions)
            pick = 0
            for i in range(n):
                try:
                    an = (action.getName(i) or "").lower()
                except Exception:
                    continue
                if an in {"click", "press", "activate", "toggle"}:
                    pick = i
                    break
            action.doAction(pick)
            return True
        except Exception:
            return False

    def set_value(
        self,
        text: str,
        automation_id: str | None = None,
        name: str | None = None,
        role: str | None = None,
    ) -> bool:
        """Replace an AT-SPI editable field's contents without typing."""
        node = self._find(automation_id=automation_id, name=name, role=role)
        if node is None:
            return False
        try:
            editable = node.queryEditableText()
            editable.setTextContents(text)
            return True
        except Exception:
            return False

    def _find(self, automation_id: str | None = None, name: str | None = None, role: str | None = None):
        import pyatspi

        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except Exception:
            return None
        frame = self._active_frame(pyatspi, desktop)
        if frame is None:
            return None
        attrs = _match_kwargs(automation_id, name, role)
        if not attrs:
            return None
        hits: list = []
        self._find_walk(pyatspi, frame, 0, attrs, hits)
        return hits[0] if hits else None

    def _find_walk(self, pyatspi, node, depth: int, attrs: dict[str, str], hits: list) -> None:
        if _matches(self._element_of(pyatspi, node), **attrs):
            hits.append(node)
            return
        if depth >= MAX_TREE_DEPTH:
            return
        for child in self._children(node):
            if hits:
                return
            self._find_walk(pyatspi, child, depth + 1, attrs, hits)


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


AX_MANUAL_ACCESSIBILITY = "AXManualAccessibility"
AX_ENHANCED_USER_INTERFACE = "AXEnhancedUserInterface"

# Chromium browsers that honour AXEnhancedUserInterface. Matched by bundle id,
# falling back to the application's AX title when the bundle id is unavailable.
CHROMIUM_BROWSER_BUNDLE_IDS = frozenset(
    {
        "com.google.Chrome",
        "com.google.Chrome.beta",
        "com.google.Chrome.dev",
        "com.google.Chrome.canary",
        "org.chromium.Chromium",
        "com.microsoft.edgemac",
        "com.microsoft.edgemac.Beta",
        "com.microsoft.edgemac.Dev",
        "com.brave.Browser",
        "com.vivaldi.Vivaldi",
        "com.operasoftware.Opera",
        "company.thebrowser.Browser",  # Arc
    }
)
CHROMIUM_BROWSER_NAMES = frozenset(
    {"Google Chrome", "Chromium", "Microsoft Edge", "Brave Browser", "Vivaldi", "Opera", "Arc"}
)

# Roles whose value set_value_at may write (text entry only).
EDITABLE_ROLES = frozenset({"TextField", "TextArea", "SecureTextField", "SearchField", "ComboBox"})

# Roles whose text can be read by position (AXRangeForPosition & friends).
TEXT_ROLES = frozenset({"TextArea", "TextField", "StaticText", "WebArea"})
_POINT_TYPES = ("kAXValueCGPointType", "kAXValueTypeCGPoint")
_RANGE_TYPES = ("kAXValueCFRangeType", "kAXValueTypeCFRange")
_RECT_TYPES = ("kAXValueCGRectType", "kAXValueTypeCGRect")


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    """A touch sweep's findings: each distinct element as its ancestor chain
    (window first), plus how many touches it took and whether the budget ran
    out. ``distinct`` is every element the finger landed on."""

    chains: tuple[tuple[axtree.AxElement, ...], ...] = ()
    probes: int = 0
    truncated: bool = False
    distinct: frozenset = frozenset()
    text_lines: int = 0


# Cap on text pulled from AXValue / AXSelectedText: a text area can hold an
# entire document, and the listing is sent to the model every step.
MAX_TEXT_CHARS = 2000


def _text_of(value) -> str:
    """A readable string for an AX value, or "" for opaque ones (AXValueRefs,
    elements, arrays). Numbers and booleans (sliders, checkboxes) are kept."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, str):
        return value[:MAX_TEXT_CHARS]
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _running_app_bundle_id(pid: int) -> str | None:
    """Bundle id of a running app via AppKit, or None when unavailable."""
    try:
        from AppKit import NSRunningApplication
    except Exception:
        return None
    try:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        bundle = app.bundleIdentifier() if app is not None else None
    except Exception:
        return None
    return str(bundle) if bundle else None


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

    def __init__(self, ax, *, bundle_id_of=None, unlock_hidden_trees: bool = True):
        self._ax = ax
        self._bundle_id_of = bundle_id_of if bundle_id_of is not None else _running_app_bundle_id
        self._unlock_hidden_trees = unlock_hidden_trees
        # pid -> [(app element, attribute, value to restore)] for every flag we
        # actually flipped; pids we already handled (changed or not) are keys.
        self._unlocked: dict[int, list[tuple[object, str, bool]]] = {}
        self._atexit_armed = False

    # -- hidden-tree unlock ------------------------------------------------------
    #
    # Chromium and Electron build their accessibility tree lazily: until an
    # assistive client asks, a Chrome page or an Electron app (VS Code, Slack,
    # Discord...) exposes little more than an empty window. Two documented
    # switches turn the tree on:
    #   * AXManualAccessibility  -- Electron's app-level switch; apps that don't
    #     know it answer kAXErrorAttributeUnsupported, so setting it is harmless;
    #   * AXEnhancedUserInterface -- the switch VoiceOver uses, honoured by Chrome
    #     and other Chromium browsers. It has side effects in some non-browser
    #     apps (window animations, window managers), so it is only set on known
    #     Chromium browsers.
    # A flag that was already on (VoiceOver running, another tool) is left
    # alone; only flags we turned on are recorded, and restore_accessibility()
    # (called when the run ends, with an atexit backstop) turns them back off.
    # Chromium builds the tree asynchronously, so the first snapshot right
    # after an unlock may still be shallow; the next one sees the full tree.

    def _focused_app(self):
        ax = self._ax
        system = ax.AXUIElementCreateSystemWide()
        app = self._attr(system, ax.kAXFocusedApplicationAttribute)
        if app is not None and self._unlock_hidden_trees:
            self._unlock(app)
        return app

    def _pid_of(self, app) -> int | None:
        get_pid = getattr(self._ax, "AXUIElementGetPid", None)
        if not callable(get_pid):
            return None
        try:
            err, pid = get_pid(app, None)
        except TypeError:
            try:
                err, pid = get_pid(app)
            except Exception:
                return None
        except Exception:
            return None
        return int(pid) if err == 0 and pid else None

    def _is_chromium_browser(self, app, pid: int | None) -> bool:
        bundle = None
        if pid is not None:
            try:
                bundle = self._bundle_id_of(pid)
            except Exception:
                bundle = None
        if bundle:
            return bundle in CHROMIUM_BROWSER_BUNDLE_IDS
        title = self._attr(app, self._ax.kAXTitleAttribute)
        return isinstance(title, str) and title in CHROMIUM_BROWSER_NAMES

    def _unlock(self, app) -> None:
        pid = self._pid_of(app)
        key = pid if pid is not None else id(app)
        if key in self._unlocked:
            return
        changed: list[tuple[object, str, bool]] = []
        wanted = [AX_MANUAL_ACCESSIBILITY]
        if self._is_chromium_browser(app, pid):
            wanted.append(AX_ENHANCED_USER_INTERFACE)
        for attribute in wanted:
            if self._attr(app, attribute) is True:
                continue  # already on (VoiceOver / another client): not ours to touch
            if self._set(app, attribute, True):
                changed.append((app, attribute, False))
        self._unlocked[key] = changed
        if changed and not self._atexit_armed:
            import atexit

            atexit.register(self.restore_accessibility)
            self._atexit_armed = True

    def _set(self, element, attribute: str, value) -> bool:
        setter = getattr(self._ax, "AXUIElementSetAttributeValue", None)
        if not callable(setter):
            return False
        try:
            return setter(element, attribute, value) == 0
        except Exception:
            return False

    def unlocked_flags(self) -> list[tuple[int, str]]:
        """(pid-or-key, attribute) for every flag currently turned on by us."""
        return [(k, attr) for k, changes in self._unlocked.items() for _app, attr, _v in changes]

    def restore_accessibility(self) -> int:
        """Turn off every flag this provider turned on. Idempotent, never raises;
        returns how many flags were restored. An app that has since quit just
        fails the write, which is fine -- its flag died with it."""
        restored = 0
        for changes in self._unlocked.values():
            for app, attribute, value in changes:
                if self._set(app, attribute, value):
                    restored += 1
        self._unlocked.clear()
        return restored

    def snapshot(self) -> list[axtree.AxElement] | None:
        ax = self._ax
        app = self._focused_app()
        if app is None:
            return None
        out: list[axtree.AxElement] = []
        windows = self._attr(app, getattr(ax, "kAXWindowsAttribute", "AXWindows"))
        walked = False
        if windows:
            for window in list(windows):
                self._walk(window, 0, out)
                walked = True
        if not walked:
            window = self._attr(app, ax.kAXFocusedWindowAttribute)
            if window is None:
                return None
            self._walk(window, 0, out)
        return out or None

    def _walk(
        self,
        element,
        depth: int,
        out: list[axtree.AxElement],
        row: int | None = None,
        ordinal: int = 0,
    ) -> None:
        """Depth-first walk. ``row`` is the AXIndex of the enclosing AXRow (if
        the parent is one) and ``ordinal`` this element's position among its
        siblings -- the fallback column when a cell has no AXColumnIndexRange."""
        el = self._element_of(element)
        child_row = None
        if el is not None:
            extra: dict = {"depth": depth}
            cell = self._table_cell(element, el.role, row, ordinal)
            if cell is not None:
                extra["table_cell"] = cell
            out.append(dataclasses.replace(el, **extra))
            if el.role == "Row":
                index = self._attr(element, "AXIndex")
                child_row = index if isinstance(index, int) and not isinstance(index, bool) else None
        if depth >= MAX_TREE_DEPTH:
            return
        for i, child in enumerate(self._children(element)):
            self._walk(child, depth + 1, out, child_row, i)

    def _table_cell(self, element, role: str, row: int | None, ordinal: int) -> tuple[int, int] | None:
        """(row, column) for a table cell: AXRowIndexRange / AXColumnIndexRange
        when the cell reports them, else the enclosing row's AXIndex and the
        cell's position in that row. None for anything that isn't a cell."""
        if role != "Cell" and row is None:
            return None
        r = self._range_location(element, "AXRowIndexRange")
        c = self._range_location(element, "AXColumnIndexRange")
        r = r if r is not None else row
        c = c if c is not None else (ordinal if row is not None else None)
        if r is None or c is None:
            return None
        return (r, c)

    def _range_location(self, element, attribute: str) -> int | None:
        value = self._attr(element, attribute)
        if value is None:
            return None
        ax = self._ax
        range_type = getattr(ax, "kAXValueCFRangeType", None)
        if range_type is None:
            range_type = getattr(ax, "kAXValueTypeCFRange", None)
        try:
            ok, rng = ax.AXValueGetValue(value, range_type, None)
        except Exception:
            return None
        loc = getattr(rng, "location", None) if ok else None
        return int(loc) if isinstance(loc, int) and loc >= 0 else None

    def _children(self, element) -> list:
        ax = self._ax
        vis = self._attr(element, getattr(ax, "kAXVisibleChildrenAttribute", "AXVisibleChildren"))
        if vis:
            return list(vis)
        kids = self._attr(element, ax.kAXChildrenAttribute)
        if kids:
            return list(kids)
        contents = self._attr(element, getattr(ax, "kAXContentsAttribute", "AXContents"))
        return list(contents) if contents else []

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
        role = str(role)
        if role.startswith("AX"):
            role = role[2:]
        name = (
            self._attr(element, ax.kAXTitleAttribute)
            or self._attr(element, ax.kAXDescriptionAttribute)
            or self._attr(element, getattr(ax, "kAXValueAttribute", "AXValue"))
            or self._attr(element, getattr(ax, "kAXRoleDescriptionAttribute", "AXRoleDescription"))
            or ""
        )
        automation_id = self._attr(element, ax.kAXIdentifierAttribute) or ""
        if geom is None:
            if not name and not automation_id:
                return None
            left = top = right = bottom = 0
        else:
            left, top, right, bottom = geom
            if right <= left or bottom <= top:
                if not name and not automation_id:
                    return None
                left = top = right = bottom = 0
        secure = role == "SecureTextField" or self._attr(element, "AXSubrole") == "AXSecureTextField"
        if secure and name == self._attr(element, getattr(ax, "kAXValueAttribute", "AXValue")):
            name = ""  # never surface a password field's value, even as its label
        return axtree.AxElement(
            role=role,
            name=str(name),
            automation_id=str(automation_id),
            bounds=(left, top, right, bottom),
            value="" if secure else _text_of(self._attr(element, getattr(ax, "kAXValueAttribute", "AXValue"))),
            selected_text="" if secure else _text_of(
                self._attr(element, getattr(ax, "kAXSelectedTextAttribute", "AXSelectedText"))
            ),
        )

    def hit_test(self, x: int, y: int) -> axtree.AxElement | None:
        """Tightest AX element whose box contains (x, y). The trackpad read."""
        _ax_el, el = self._hit_ax(x, y)
        return el

    def press_at(self, x: int, y: int) -> bool:
        """AXPress the deepest node under (x, y). Never HID / CGEvent.

        Prefer the OS finger `AXUIElementCopyElementAtPosition` (true trackpad
        hit). Fall back to walking AXPosition/AXSize boxes when the copy-at
        API is missing or returns nothing.
        """
        app = self._focused_app()
        el = self._copy_at(app, x, y)
        if el is not None:
            return self._ax_press(el)
        ax_el, _el = self._hit_ax(x, y)
        if ax_el is None:
            return False
        return self._ax_press(ax_el)

    # -- touch: the OS finger --------------------------------------------------
    #
    # AXUIElementCopyElementAtPosition is the hit test VoiceOver's Trackpad
    # Commander uses: the deepest element under a point, respecting window
    # z-order. It is asked of the *application* element, never the system-wide
    # one (system-wide hit tests are reported to stall SwiftUI scroll views).

    def _copy_at(self, app, x: float, y: float):
        """The AXUIElement under (x, y) in ``app``, or None."""
        copy_at = getattr(self._ax, "AXUIElementCopyElementAtPosition", None)
        if app is None or not callable(copy_at):
            return None
        try:
            err, el = copy_at(app, float(x), float(y), None)
        except TypeError:
            try:
                err, el = copy_at(app, float(x), float(y))
            except Exception:
                return None
        except Exception:
            return None
        return el if err == 0 and el is not None else None

    def _chain_of(self, element) -> list[tuple[object, axtree.AxElement]]:
        """(AXUIElement, AxElement) from the window down to ``element``, via
        AXParent. Elements that don't map (no role / no box) are skipped; the
        walk stops at the window, the application, or MAX_TREE_DEPTH."""
        parent_attr = getattr(self._ax, "kAXParentAttribute", "AXParent")
        chain: list[tuple[object, axtree.AxElement]] = []
        cur = element
        for _ in range(MAX_TREE_DEPTH):
            if cur is None:
                break
            el = self._element_of(cur)
            if el is not None:
                if el.role == "Application":
                    break
                chain.append((cur, dataclasses.replace(el, origin="hit-test")))
                if el.role == "Window":
                    break
            cur = self._attr(cur, parent_attr)
        chain.reverse()
        return chain

    def probe(
        self,
        region: tuple[int, int, int, int],
        *,
        max_probes: int = touch_probe.DEFAULT_MAX_PROBES,
        min_cell: int = touch_probe.DEFAULT_MIN_CELL,
        max_seconds: float = touch_probe.DEFAULT_MAX_SECONDS,
        read_text: bool = False,
    ) -> ProbeResult:
        """Touch-explore ``region`` (left, top, right, bottom): hit-test an
        adaptive grid, and return each distinct element found as its ancestor
        chain (window first), ready for :func:`touch_probe.merge_chains`.

        With ``read_text``, text elements that were touched are also read at
        the touched points, line by line (see :meth:`_line_at`); each line is
        a "TextLine" child of its text element."""
        app = self._focused_app()
        handles: dict[axtree.AxElement, object] = {}

        def hit(x: int, y: int):
            ax_el = self._copy_at(app, x, y)
            if ax_el is None:
                return None
            el = self._element_of(ax_el)
            if el is None:
                return None
            handles.setdefault(el, ax_el)
            return el

        sweep = touch_probe.adaptive_probe(
            region, hit, max_probes=max_probes, min_cell=min_cell, max_seconds=max_seconds
        )
        chains = []
        for ax_el in handles.values():
            chain = [e for _h, e in self._chain_of(ax_el)]
            if chain:
                chains.append(chain)
        lines: dict[tuple, axtree.AxElement] = {}
        if read_text:
            for point, el in sweep.hits.items():
                if el is None or el.role not in TEXT_ROLES:
                    continue
                line = self._line_at(handles[el], *point)
                if line is not None:
                    key, line_el = line
                    if (el, key) not in lines:
                        lines[(el, key)] = line_el
            for (el, _key), line_el in lines.items():
                base = next((c for c in chains if c and c[-1] == el), [el])
                chains.append([*base, line_el])
        return ProbeResult(
            chains=tuple(tuple(c) for c in chains),
            probes=sweep.probes,
            truncated=sweep.truncated,
            distinct=frozenset(sweep.distinct()),
            text_lines=len(lines),
        )

    def _param(self, element, attribute: str, parameter):
        fn = getattr(self._ax, "AXUIElementCopyParameterizedAttributeValue", None)
        if not callable(fn):
            return None
        try:
            err, value = fn(element, attribute, parameter, None)
        except Exception:
            return None
        return value if err == 0 else None

    def _make_value(self, type_names: tuple[str, ...], value):
        ax = self._ax
        create = getattr(ax, "AXValueCreate", None)
        vtype = next((getattr(ax, n) for n in type_names if hasattr(ax, n)), None)
        if not callable(create) or vtype is None:
            return None
        try:
            return create(vtype, value)
        except Exception:
            return None

    def _unwrap(self, value, type_names: tuple[str, ...]):
        ax = self._ax
        vtype = next((getattr(ax, n) for n in type_names if hasattr(ax, n)), None)
        try:
            ok, inner = ax.AXValueGetValue(value, vtype, None)
        except Exception:
            return None
        return inner if ok else None

    def _line_at(self, element, x: int, y: int) -> tuple[int, axtree.AxElement] | None:
        """Read the text line under (x, y) in a text element, the way VoiceOver
        speaks "text under the pointer": AXRangeForPosition -> character index
        -> AXLineForIndex -> AXRangeForLine -> AXStringForRange (+ its
        AXBoundsForRange). Returns (line number, "TextLine" element) or None
        when the element doesn't support these parameterized attributes (some
        Chromium/Electron fields don't)."""
        point = self._make_value(_POINT_TYPES, (float(x), float(y)))
        if point is None:
            return None
        at = self._param(element, "AXRangeForPosition", point)
        rng = self._unwrap(at, _RANGE_TYPES) if at is not None else None
        index = getattr(rng, "location", None)
        if not isinstance(index, int) or index < 0:
            return None
        line = self._param(element, "AXLineForIndex", index)
        if not isinstance(line, int) or isinstance(line, bool) or line < 0:
            return None
        line_range = self._param(element, "AXRangeForLine", line)
        if line_range is None:
            return None
        text = self._param(element, "AXStringForRange", line_range)
        if not isinstance(text, str) or not text.strip():
            return None
        bounds = (0, 0, 0, 0)
        rect_value = self._param(element, "AXBoundsForRange", line_range)
        rect = self._unwrap(rect_value, _RECT_TYPES) if rect_value is not None else None
        if rect is not None:
            try:
                left, top = int(rect.origin.x), int(rect.origin.y)
                bounds = (left, top, left + int(rect.size.width), top + int(rect.size.height))
            except Exception:
                bounds = (0, 0, 0, 0)
        text = text.strip()[:MAX_TEXT_CHARS]
        return line, axtree.AxElement(
            role="TextLine",
            name=text,
            automation_id="",
            bounds=bounds,
            value=text,
            origin="touch-text",
        )

    def occluder_of(self, target: axtree.AxElement) -> axtree.AxElement | None:
        """What is on top of ``target``'s centre, if it isn't ``target``.

        Touches the centre with the OS hit test. Not occluded when the touch
        lands on the target, on something inside it (a button's label), or on
        an ancestor that contains it (a container that isn't hit-testable per
        child). Returns the covering element (e.g. a modal sheet) otherwise,
        and None when the touch can't be made -- unknown is not "blocked"."""
        if target.area <= 0:
            return None
        app = self._focused_app()
        x, y = target.center
        ax_el = self._copy_at(app, x, y)
        if ax_el is None:
            return None
        chain = self._chain_of(ax_el)
        if not chain or any(el == target for _h, el in chain):
            return None
        top_handle, top = chain[-1]
        if self._subtree_contains(top_handle, target):
            return None
        return dataclasses.replace(top, origin="tree")

    def _subtree_contains(self, element, target: axtree.AxElement, budget: int = 400) -> bool:
        stack = [(element, 0)]
        seen = 0
        while stack and seen < budget:
            cur, depth = stack.pop()
            seen += 1
            if self._element_of(cur) == target:
                return True
            if depth < MAX_TREE_DEPTH:
                stack.extend((c, depth + 1) for c in self._children(cur))
        return False

    def set_value_at(self, x: int, y: int, text: str) -> bool:
        """Write AXValue on the element under (x, y) -- for a field found by
        touch that the tree walk (and so ``set_value``'s search) can't reach.
        Only writes when the element on top at that point is a text-entry role;
        anything else (a button, a sheet that slid over the field) is refused."""
        app = self._focused_app()
        ax_el = self._copy_at(app, x, y)
        if ax_el is None:
            return False
        el = self._element_of(ax_el)
        if el is None or el.role not in EDITABLE_ROLES:
            return False
        attr = getattr(self._ax, "kAXValueAttribute", "AXValue")
        return self._set(ax_el, attr, text)

    def _ax_press(self, ax_el) -> bool:
        action = getattr(self._ax, "kAXPressAction", "AXPress")
        confirm = getattr(self._ax, "kAXConfirmAction", "AXConfirm")
        try:
            err = self._ax.AXUIElementPerformAction(ax_el, action)
            if err == 0:
                return True
            err = self._ax.AXUIElementPerformAction(ax_el, confirm)
            return err == 0
        except Exception:
            return False

    def _hit_ax(self, x: int, y: int):
        """(AXUIElement, AxElement) of the smallest box containing (x, y)."""
        ax = self._ax
        app = self._focused_app()
        if app is None:
            return None, None
        window = self._attr(app, ax.kAXFocusedWindowAttribute)
        windows = self._attr(app, getattr(ax, "kAXWindowsAttribute", "AXWindows"))
        roots = list(windows) if windows else ([window] if window is not None else [])
        if not roots:
            return None, None
        best_ax = None
        best_el = None
        best_area = None
        best_depth = -1

        def walk(element, depth: int) -> None:
            nonlocal best_ax, best_el, best_area, best_depth
            el = self._element_of(element)
            if el is not None and el.contains(x, y) and el.area > 0:
                if (
                    best_el is None
                    or el.area < best_area
                    or (el.area == best_area and depth > best_depth)
                ):
                    best_ax = element
                    best_el = el
                    best_area = el.area
                    best_depth = depth
            if depth >= MAX_TREE_DEPTH:
                return
            for child in self._children(element):
                walk(child, depth + 1)

        for root in roots:
            walk(root, 0)
        return best_ax, best_el

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

    def press(self, automation_id: str | None = None, name: str | None = None, role: str | None = None) -> bool:
        """AXPress the matching element without moving the cursor."""
        element = self._find(automation_id=automation_id, name=name, role=role)
        if element is None:
            return False
        action = getattr(self._ax, "kAXPressAction", "AXPress")
        try:
            err = self._ax.AXUIElementPerformAction(element, action)
            return err == 0
        except Exception:
            return False

    def set_value(
        self,
        text: str,
        automation_id: str | None = None,
        name: str | None = None,
        role: str | None = None,
    ) -> bool:
        """Write kAXValueAttribute on a text field without typing."""
        element = self._find(automation_id=automation_id, name=name, role=role)
        if element is None:
            return False
        attr = getattr(self._ax, "kAXValueAttribute", "AXValue")
        try:
            err = self._ax.AXUIElementSetAttributeValue(element, attr, text)
            return err == 0
        except Exception:
            return False

    def _find(self, automation_id: str | None = None, name: str | None = None, role: str | None = None):
        ax = self._ax
        app = self._focused_app()
        if app is None:
            return None
        window = self._attr(app, ax.kAXFocusedWindowAttribute)
        windows = self._attr(app, getattr(ax, "kAXWindowsAttribute", "AXWindows"))
        roots = list(windows) if windows else ([window] if window is not None else [])
        if not roots:
            return None
        attrs = _match_kwargs(automation_id, name, role)
        if not attrs:
            return None
        hits: list = []
        for root in roots:
            self._find_walk(root, 0, attrs, hits)
            if hits:
                return hits[0]
        return None

    def _find_walk(self, element, depth: int, attrs: dict[str, str], hits: list) -> None:
        if _matches(self._element_of(element), **attrs):
            hits.append(element)
            return
        if depth >= MAX_TREE_DEPTH:
            return
        for child in self._children(element):
            if hits:
                return
            self._find_walk(child, depth + 1, attrs, hits)


def query_pad_grants() -> dict[str, object]:
    """TCC / OS grants for the pad. Never HID. Never elevate.

    pad: "ax" | "cgwindow" | "uia" | "memory"
    """
    if sys.platform.startswith("win"):
        return {
            "accessibility": True,
            "screen_recording": False,
            "pad": "uia",
            "detail": "Windows: UIA tree is the pad. No TCC.",
        }
    if sys.platform != "darwin":
        return {
            "accessibility": False,
            "screen_recording": False,
            "pad": "memory",
            "detail": "Linux: no AT-SPI mutate. Memory inspect is the live path.",
        }
    ax_ok = False
    rec_ok = False
    try:
        from ApplicationServices import AXIsProcessTrusted

        ax_ok = bool(AXIsProcessTrusted())
    except Exception:
        ax_ok = False
    try:
        from Quartz import CGPreflightScreenCaptureAccess

        rec_ok = bool(CGPreflightScreenCaptureAccess())
    except Exception:
        rec_ok = False
    pad = "ax" if ax_ok else "cgwindow"
    if ax_ok:
        detail = (
            "macOS: AX tree (fine pad) + CopyElementAtPosition (OS finger). "
            "AXPress tap. HID refused."
        )
    else:
        detail = (
            "macOS: Accessibility off — coarse pad is CGWindow bounds (no Screen "
            "Recording needed). Grant Accessibility on the host app (Terminal / "
            ".app) in System Settings → Privacy & Security → Accessibility. "
            "Screen Recording only fills titles + pixel-diff verify. Never HID."
        )
    return {
        "accessibility": ax_ok,
        "screen_recording": rec_ok,
        "pad": pad,
        "detail": detail,
    }


def request_pad_grants() -> dict[str, object]:
    """Prompt TCC for Accessibility + Screen Recording. Never fail-closed.

    Accessibility is the pad. Screen Recording is titles + verify only.
    Prompting happens on THIS process — TCC keys off the host app
    (Terminal / .app), not a child binary. Never HID, never elevate.
    """
    if sys.platform != "darwin":
        return query_pad_grants()
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
        try:
            from ApplicationServices import AXIsProcessTrustedWithOptions

            AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
        except Exception:
            pass
    try:
        from Quartz import CGRequestScreenCaptureAccess

        CGRequestScreenCaptureAccess()
    except Exception:
        pass
    grants = query_pad_grants()
    if not grants.get("accessibility"):
        try:
            import subprocess

            subprocess.Popen(
                [
                    "open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    return grants

