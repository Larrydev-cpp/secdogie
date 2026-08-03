"""Present a desktop accessibility snapshot to the model as a list of named
targets, and resolve the model's pick back to a real screen point.

This is the *live-loop* counterpart to axtree.py. axtree is the identity/geometry
brain used by macro **replay** (find the element under a recorded point, re-find
it later by identity). Here the model isn't replaying anything -- it's deciding
live -- so instead of guessing a pixel from the screenshot it can be handed the
foreground window's interactable elements, each with a stable ref like ``e3``,
and reply with ``{"action": "click_element", "element": "e3"}``. We turn that ref
back into the element's centre (real screen pixels, ready for a left_click).

That's the "don't go pure vision" path for desktop control: the accessibility
tree *knows* a button is there with exact bounds, so clicking it by identity is
more reliable than asking a vision model to guess an (x, y) off a downscaled
frame -- no coordinate-scaling round-off, no near-miss.

Deliberately OS-free and side-effect-free: reading the live tree is
desktop_ax.py's job (the on-machine seam); here we only *shape* a snapshot into a
listing and *resolve* a ref, so both halves unit-test without a desktop -- the
same split axtree/desktop_ax already use.
"""
from __future__ import annotations

import re

from .axtree import AxElement, selector_for

# Roles worth offering the model as click targets. A desktop tree is dense and
# mostly structural (windows, panes, groups); surfacing every node would drown
# the model, so we keep the interactable leaves. All three platform vocabularies
# are listed because the providers report roles differently: Windows UIA yields
# PascalCase with the "Control" suffix trimmed by desktop_ax ("Button", "Edit"),
# Linux AT-SPI yields getRoleName() strings ("push button", "entry"), macOS AX
# yields "AXButton"/"AXTextField" with the "AX" prefix trimmed ("Button",
# "TextField"). Matching is case-insensitive. This set is a starting point --
# tune it on your machine as you see which roles actually matter for your apps.
INTERACTABLE_ROLES = frozenset(
    {
        # Windows UI Automation (ControlTypeName, "Control" suffix already trimmed)
        "button",
        "splitbutton",
        "edit",
        "checkbox",
        "radiobutton",
        "combobox",
        "hyperlink",
        "menuitem",
        "listitem",
        "tabitem",
        "treeitem",
        "slider",
        "spinner",
        # Linux AT-SPI (Accessible.getRoleName())
        "push button",
        "toggle button",
        "check box",
        "radio button",
        "combo box",
        "link",
        "menu item",
        "list item",
        "page tab",
        "tree item",
        "entry",
        "text",
        "password text",
        "spin button",
        # macOS AX (AXRole, "AX" prefix already trimmed by desktop_ax)
        "textfield",
        "textarea",
        "securetextfield",
        "popupbutton",
        "menubutton",
        "menubaritem",
        "disclosuretriangle",
        "incrementor",
        "stepper",
        "tab",
    }
)


def is_target(el: AxElement) -> bool:
    """True if `el` is worth showing the model as a clickable target: it has an
    interactable role AND something to identify it by (a name or automation-id).
    An anonymous control can't be described in the listing OR re-found, so it's
    dropped -- the model can still fall back to a plain coordinate click for it.
    Reuses axtree.selector_for as the single definition of "identifiable"."""
    return el.role.strip().lower() in INTERACTABLE_ROLES and selector_for(el) is not None


def interactable_targets(elements: list[AxElement]) -> list[AxElement]:
    """The subset of a snapshot worth presenting, in the snapshot's own order
    (which the providers produce by a stable top-down tree walk, so the same UI
    yields the same order -- and therefore the same refs -- across steps)."""
    return [el for el in elements if is_target(el)]


def render_for_model(targets: list[AxElement]) -> str:
    """Format `targets` as the block appended to the model's task, or "" when
    there are none (the caller then appends nothing and the step is unchanged).
    Each line is `[eN] Role "name" (id=...)`, where `eN` is the ref the model
    passes back in a click_element action -- 1-based to match how the list reads."""
    if not targets:
        return ""
    lines = []
    for i, el in enumerate(targets, 1):
        label = el.name or (el.automation_id and f"id={el.automation_id}") or "(unnamed)"
        # Show the automation-id too when it exists alongside a visible name -- it
        # disambiguates lookalikes ("OK" appearing twice) at a glance.
        extra = f" (id={el.automation_id})" if el.automation_id and el.name else ""
        lines.append(f'  [e{i}] {el.role} "{label}"{extra}')
    return (
        "Interactable elements detected on screen (from the accessibility tree). "
        'To click one, reply {"action": "click_element", "element": "eN", ...} '
        "using its ref below -- this hits the element's true bounds, so prefer it "
        "over guessing a pixel when your target is listed:\n" + "\n".join(lines)
    )


_REF_RE = re.compile(r"^\s*e?(\d+)\s*$", re.IGNORECASE)


def resolve_ref(targets: list[AxElement], ref: str | int | None) -> AxElement | None:
    """Map a model-supplied element ref back to an AxElement in `targets`, or
    None if it doesn't name a listed element. Accepts the ``eN`` form the listing
    uses, a bare integer, or the same with surrounding whitespace; refs are
    1-based. Anything else (a name, an out-of-range index, junk) -> None, so the
    caller reports a miss to the model rather than clicking a wrong element.

    `targets` must be the *same* list that produced the listing this ref came
    from -- resolve against the step's cached targets, not a fresh snapshot, so a
    ref means exactly the row the model saw even if the UI shifted meanwhile."""
    if ref is None:
        return None
    if isinstance(ref, bool):  # bool is an int subclass; a stray True/False isn't a ref
        return None
    if isinstance(ref, int):
        idx = ref
    else:
        match = _REF_RE.match(str(ref))
        if match is None:
            return None
        idx = int(match.group(1))
    if 1 <= idx <= len(targets):
        return targets[idx - 1]
    return None


def point_for_ref(targets: list[AxElement], ref: str | int | None) -> tuple[int, int] | None:
    """The real-screen-pixel centre to click for a model element ref, or None if
    the ref doesn't resolve. Thin wrapper over resolve_ref + AxElement.center so
    the loop never touches AxElement directly."""
    el = resolve_ref(targets, ref)
    return el.center if el is not None else None
