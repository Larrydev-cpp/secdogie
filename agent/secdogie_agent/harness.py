"""Desktop harness: which live-loop steps can skip pixels and skip the mouse.

`--desktop-ax` already lists interactable elements so the model can click by
ref. Until now that was still a *pixel* click: `click_element` was rewritten
to `left_click` at the element's centre, which steals the real cursor and
needs the window frontmost.

This module is the next seam. Given a listed AxElement, we prefer delivering
the action through the accessibility API itself (Invoke / AXPress / AT-SPI
click, SetValue) so:

1. the real mouse does not move (less invasive),
2. the window does not have to steal focus,
3. the screenshot does not have to go to the model (tokens).

Deliberately OS-free and side-effect-free -- the same split axtree/desktop_ax
already use. The on-machine half (actually calling Invoke) lives on the
optional `press` / `set_value` methods of a DesktopAxProvider. Here we only
classify roles and decide whether this step can omit the image.

CAD canvases, games, and custom-drawn chrome have no named widgets: the tree
comes back empty (or the model emits `look`) and the existing vision path
runs unchanged. That's the "in some parts" rule -- chrome/dialogs/menus go
through the harness; pixels remain the fallback, not the default.
"""
from __future__ import annotations

import sys

from .axtree import AxElement

# Roles whose value can be set without synthesizing keystrokes. Same three
# platform vocabularies as elements.INTERACTABLE_ROLES (Windows / AT-SPI / macOS).
EDIT_ROLES = frozenset(
    {
        "edit",
        "entry",
        "text",
        "password text",
        "textfield",
        "textarea",
        "securetextfield",
    }
)

# Actions that only make sense against a picture. On an accessibility-only
# turn the loop refuses these and asks the model to pick a listed ref or
# `look` instead of guessing coordinates of an image it was never shown.
# Darwin left_click / double_click are NOT in this set at refuse-time:
# they are trackpad taps (AX hit-test + AXPress).
PIXEL_KINDS = frozenset(
    {
        "left_click",
        "right_click",
        "double_click",
        "move",
        "drag",
        "scroll",
        "track_click",
    }
)

# Darwin: these kinds are a finger on the AX pad, not a screenshot click.
TOUCH_KINDS = frozenset({"left_click", "double_click", "click_element"})

OMIT_IMAGE_NOTE = (
    "ACCESSIBILITY-ONLY VIEW: no screenshot is attached this step. The element "
    "list above is the live UI (re-read this turn). Prefer "
    '{"action": "click_element", "element": "eN"} or '
    '{"action": "type", "element": "eN", "text": "..."} -- those are delivered '
    "through the accessibility API and do not move the real cursor. On macOS "
    "the listing is a trackpad: "
    '{"action": "left_click", "x": ..., "y": ...} hit-tests the AX tree and '
    "AXPresses (never HID). If your target is not listed (a CAD canvas, a "
    'custom-drawn control, fine text), reply {"action": "look"} to get pixels.'
)


def is_editable(el: AxElement) -> bool:
    """True if `el` is a text field we can fill via SetValue rather than typing."""
    return el.role.strip().lower() in EDIT_ROLES


def should_omit_screenshot(
    targets: list[AxElement],
    *,
    refresh_view: bool,
    boost_detail: bool,
    platform: str | None = None,
) -> bool:
    """True when the AX listing is enough for this step: skip the image block.

    Always send pixels on the first frame / after `look` (`refresh_view`),
    after a miss (`boost_detail`), or when the tree is empty (CAD canvas,
    games, custom-drawn UI -- vision is the only sense). Subsequent steps
    with a healthy listing omit the image: that's the token win, and the
    model can still `look` the moment pixels actually matter.

    Darwin is included. The AX tree is the trackpad (names, roles, bounds);
    `left_click` hit-tests it and AXPresses. Pixels are verify / `look`, not
    the pad.
    """
    if refresh_view or boost_detail:
        return False
    _ = platform or sys.platform
    return bool(targets)


def press_point(backend, x: int, y: int) -> str | None:
    """Tap the accessibility node under (x, y). Darwin's trackpad path.

    Tries `provider.press_at` (live AXUIElement) first, then snapshot +
    smallest-box hit-test + `press` / `invoke_element`. Never HID. None if
    nothing is under the point or the provider cannot press.
    """
    provider = getattr(backend, "ax_provider", None)
    if provider is None:
        return None
    press_at = getattr(provider, "press_at", None)
    if callable(press_at):
        try:
            if press_at(int(x), int(y)):
                return f"AXPress at ({int(x)}, {int(y)}) via hit-test (no HID)"
        except Exception:
            pass
    try:
        snapshot = provider.snapshot()
    except Exception:
        snapshot = None
    if not snapshot:
        return None
    from . import axtree

    el = axtree.element_at(snapshot, int(x), int(y))
    if el is None:
        return None
    invoker = getattr(backend, "invoke_element", None)
    if callable(invoker):
        result = invoker(el)
        if result is not None:
            return result
    press = getattr(provider, "press", None)
    if not callable(press):
        return None
    attrs = axtree.selector_for(el)
    if not attrs:
        return None
    try:
        ok = press(**attrs)
    except Exception:
        return None
    if not ok:
        return None
    label = el.name or el.automation_id or el.role
    return f"AXPress {el.role} {label!r} at ({int(x)}, {int(y)}) (no HID)"
