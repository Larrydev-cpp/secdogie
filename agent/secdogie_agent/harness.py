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

OMIT_IMAGE_NOTE = (
    "ACCESSIBILITY-ONLY VIEW: no screenshot is attached this step. The element "
    "list above is the live UI (re-read this turn). Prefer "
    '{"action": "click_element", "element": "eN"} or '
    '{"action": "type", "element": "eN", "text": "..."} -- those are delivered '
    "through the accessibility API and do not move the real cursor. If your "
    "target is not listed (a CAD canvas, a custom-drawn control, fine text), "
    'reply {"action": "look"} to get pixels.'
)


def is_editable(el: AxElement) -> bool:
    """True if `el` is a text field we can fill via SetValue rather than typing."""
    return el.role.strip().lower() in EDIT_ROLES


def should_omit_screenshot(
    targets: list[AxElement],
    *,
    refresh_view: bool,
    boost_detail: bool,
) -> bool:
    """True when the AX listing is enough for this step: skip the image block.

    Always send pixels on the first frame / after `look` (`refresh_view`),
    after a miss (`boost_detail`), or when the tree is empty (CAD canvas,
    games, custom-drawn UI -- vision is the only sense). Subsequent steps
    with a healthy listing omit the image: that's the token win, and the
    model can still `look` the moment pixels actually matter.
    """
    if refresh_view or boost_detail:
        return False
    return bool(targets)
