"""Keyboard focus traversal: reach a target by walking the focus ring, not by
clicking a pixel.

Some targets can't be reached by a coordinate click -- a control inside a
custom-drawn region the tree can't place, a widget behind occlusion, anything on
a headless-but-focusable surface. The keyboard reaches them the way a keyboard
user does: press Tab / Shift+Tab (or arrow keys) to move keyboard focus, and
after each keystroke read *which element now holds focus* from the accessibility
tree (Windows UIA ``HasKeyboardFocus``, AT-SPI ``FOCUSED`` state, macOS
``AXFocusedUIElement``). No pixels are involved and the ring itself decides where
focus can go, so this never lands somewhere unfocusable.

This module is the OS-free planner: it drives two injected callables -- one that
sends a key, one that reads the currently focused element -- and stops when the
target is focused, the ring cycles back on itself, or a step budget runs out.
Deterministic and side-effect-free given those callables, so the whole policy is
unit-tested headless.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from .axtree import AxElement

# The keys that move keyboard focus, in the order to try them. Tab is the
# primary ring; the arrows cover grids / radio groups / menus that Tab skips.
DEFAULT_FORWARD = ("tab",)
DEFAULT_KEYS = ("tab", "shift+tab", "down", "up", "right", "left")

# A focus walk is bounded: a keyboard user never tabs forever, and a ring that
# large is a fault, not something to grind through.
DEFAULT_MAX_STEPS = 200

# What identifies "the same focus stop" for cycle detection: identity, never the
# live value (a field's text may change as we pass through it).
FocusKey = tuple[str, str, str]  # (role, name, automation_id)

Matcher = Callable[[AxElement], bool]
ReadFocus = Callable[[], "AxElement | None"]
SendKey = Callable[[str], bool]  # returns whether the key was delivered


def _key(el: AxElement) -> FocusKey:
    return (el.role, el.name, el.automation_id)


def match_by(
    *, automation_id: str | None = None, name: str | None = None, role: str | None = None
) -> Matcher:
    """A matcher over identity attributes, case-insensitive exact (like
    ``axtree.find_elements``). Blank/omitted fields are ignored; at least one
    must be given, or nothing can ever match."""
    want_id = automation_id.casefold() if automation_id else None
    want_name = name.casefold() if name else None
    want_role = role.casefold() if role else None
    if want_id is None and want_name is None and want_role is None:
        raise ValueError("match_by needs at least one of automation_id / name / role")

    def matcher(el: AxElement) -> bool:
        if want_id is not None and el.automation_id.casefold() != want_id:
            return False
        if want_name is not None and el.name.casefold() != want_name:
            return False
        if want_role is not None and el.role.casefold() != want_role:
            return False
        return True

    return matcher


class Outcome(str, Enum):
    ALREADY = "already"  # the target already had focus; no keys sent
    REACHED = "reached"  # a keystroke moved focus onto the target
    CYCLED = "cycled"  # focus returned to a stop already seen without hitting the target
    EXHAUSTED = "exhausted"  # the step budget ran out first
    NO_FOCUS = "no-focus"  # focus could not be read (no keyboard-focus sense here)
    STUCK = "stuck"  # a key was sent but focus did not move and no key advances it


@dataclass(frozen=True)
class FocusResult:
    outcome: Outcome
    focused: AxElement | None  # where focus ended up (None if it was never readable)
    steps: int  # keystrokes actually sent
    path: tuple[FocusKey, ...] = ()  # the focus stops visited, in order

    @property
    def ok(self) -> bool:
        return self.outcome in (Outcome.ALREADY, Outcome.REACHED)


def navigate_to(
    target: Matcher,
    read_focus: ReadFocus,
    send_key: SendKey,
    *,
    keys: Sequence[str] = DEFAULT_FORWARD,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> FocusResult:
    """Drive ``keys`` until ``target`` holds focus.

    Strategy: read focus; if it matches, done. Otherwise send the first key and
    re-read. As long as focus keeps landing on *new* stops, keep pressing the
    same key -- that is the ring advancing. When a key brings focus back to a
    stop already seen (the ring wrapped) without a match, move on to the next
    key in ``keys`` from here; when every key is spent, report how it ended.
    Never sends a key that isn't in ``keys``; never presses past ``max_steps``.

    ``keys`` defaults to Tab only (the forward ring); pass ``DEFAULT_KEYS`` to
    also try Shift+Tab and the arrows for grids and radio groups."""
    if not keys:
        raise ValueError("navigate_to needs at least one key to try")
    focused = read_focus()
    if focused is None:
        return FocusResult(Outcome.NO_FOCUS, None, 0)
    if target(focused):
        return FocusResult(Outcome.ALREADY, focused, 0, (_key(focused),))

    seen: set[FocusKey] = {_key(focused)}
    path: list[FocusKey] = [_key(focused)]
    steps = 0
    for key in keys:
        while steps < max_steps:
            if not send_key(key):
                break  # this key can't be delivered; try the next one
            steps += 1
            moved = read_focus()
            if moved is None:
                return FocusResult(Outcome.NO_FOCUS, None, steps, tuple(path))
            k = _key(moved)
            if target(moved):
                path.append(k)
                return FocusResult(Outcome.REACHED, moved, steps, tuple(path))
            if k in seen:
                focused = moved
                break  # this key's ring wrapped without a hit; switch keys
            seen.add(k)
            path.append(k)
            focused = moved
        if steps >= max_steps:
            return FocusResult(Outcome.EXHAUSTED, focused, steps, tuple(path))
    # Every key exhausted its ring. If nothing ever moved, we're stuck; else the
    # target simply isn't on any ring we can walk.
    outcome = Outcome.STUCK if steps == 0 else Outcome.CYCLED
    return FocusResult(outcome, focused, steps, tuple(path))


@dataclass(frozen=True)
class FocusNavigator:
    """Bind a provider's focus reader and a backend's key sender once, then
    :meth:`to` a target repeatedly. ``read_focus`` is
    ``provider.focused_node``; ``send_key`` sends one key chord without moving
    the mouse."""

    read_focus: ReadFocus
    send_key: SendKey
    keys: Sequence[str] = field(default=DEFAULT_KEYS)
    max_steps: int = DEFAULT_MAX_STEPS

    def to(self, **attrs) -> FocusResult:
        """Navigate to the element matching ``automation_id`` / ``name`` /
        ``role`` (see :func:`match_by`)."""
        return navigate_to(
            match_by(**attrs), self.read_focus, self.send_key, keys=self.keys, max_steps=self.max_steps
        )

    def current(self) -> AxElement | None:
        return self.read_focus()


__all__ = [
    "DEFAULT_FORWARD",
    "DEFAULT_KEYS",
    "DEFAULT_MAX_STEPS",
    "Outcome",
    "FocusResult",
    "FocusNavigator",
    "navigate_to",
    "match_by",
]
