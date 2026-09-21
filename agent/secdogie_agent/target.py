"""AX opaque target reference + generation-gated re-resolution (Phase 2.5).

Between observing a window and acting on it, the tree can move. The re-find
seams in the agent today re-resolve a target by fuzzy (automation_id/name/role)
match with no version check, so a moved element -- or a same-named "lookalike"
that appeared meanwhile -- can get clicked. That is a time-of-check/time-of-use
(TOCTOU) hole. This module closes it:

  * **generation gate** -- an ``AXTargetRef`` records the window generation it
    was captured in (the monotonic counter from ``observation.py``). If the
    current generation is newer, the reference is ``STALE`` and the caller must
    re-observe. No fuzzy re-search is attempted on a stale ref.
  * **uniqueness guard** -- a re-find that matches two or more elements is
    ``AMBIGUOUS`` and refused, never resolved to "the first one".
  * **optional native identity** -- when a provider supplies a stable native
    token per element (Windows UIA RuntimeId / AT-SPI path / macOS AXUIElement),
    it disambiguates lookalikes exactly. The pure layer treats it as an opaque
    key, so this module stays headless; wiring the real token in is Phase 2.7.

Pure and read-only: it only ever *refuses* to act on a target it cannot still
identity-match at the right generation (fail-closed toward re-observe). It adds
no capability and no new primitive -- it strengthens the safety boundary rather
than widening it. The identity core is duck-typed over ``role``/``name``/
``automation_id`` so it serves both ``axtree.AxElement`` and ``atlas.ControlNode``.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

# Verdicts.
VALID = "valid"          # exactly one identity match at the right generation
STALE = "stale"          # generation advanced -> re-observe, do not re-search
AMBIGUOUS = "ambiguous"  # >1 identity match -> refuse, do not guess
GONE = "gone"            # 0 identity match -> the element is no longer present
MOVED = "moved"          # unique match but its bounds shifted beyond tolerance


class _Identifiable(Protocol):
    role: str
    name: str
    automation_id: str


def gen_gate(observed_generation: int, current_generation: int) -> str | None:
    """``STALE`` when the observed generation is not the current one, else None.

    Any mismatch means the reference was captured against a different snapshot
    than the one we would act on, so it must be re-observed -- including the
    anomalous "ref from a newer generation than the world" case, which we also
    refuse rather than trust."""
    return STALE if observed_generation != current_generation else None


def _matches(el: _Identifiable, automation_id: str, name: str, role: str) -> bool:
    if automation_id and el.automation_id.casefold() != automation_id.casefold():
        return False
    if name and el.name.casefold() != name.casefold():
        return False
    if role and el.role.casefold() != role.casefold():
        return False
    return True


def unique_identity_match(
    snapshot: Sequence[_Identifiable], *, automation_id: str = "", name: str = "", role: str = ""
) -> tuple[str, _Identifiable | None]:
    """Exact, case-insensitive identity match with a uniqueness guard.

    Returns ``(VALID, el)`` only when exactly one element matches; ``(GONE,
    None)`` for zero; ``(AMBIGUOUS, None)`` for two or more. An empty selector
    (no attribute given) matches nothing -- ``(GONE, None)`` -- so a blank query
    never latches onto an element, matching ``atlas.find_control``/
    ``axtree.find_elements`` semantics."""
    if not (automation_id or name or role):
        return (GONE, None)
    found: _Identifiable | None = None
    for el in snapshot:
        if not _matches(el, automation_id, name, role):
            continue
        if found is not None:
            return (AMBIGUOUS, None)
        found = el
    return (VALID, found) if found is not None else (GONE, None)


def _center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return ((left + right) // 2, (top + bottom) // 2)


@dataclass(frozen=True)
class AXTargetRef:
    """An opaque handle to an element observed at a known window generation.

    ``bounds`` is the ``axtree.AxElement`` convention ``(left, top, right,
    bottom)`` in real screen pixels. ``native_identity`` is a provider-supplied
    stable token (empty until Phase 2.7 wires it); the pure layer only ever
    compares it for exact equality."""

    window_id: int
    app_pid: int
    generation: int
    role: str = ""
    name: str = ""
    automation_id: str = ""
    native_identity: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def center(self) -> tuple[int, int]:
        return _center(self.bounds)

    @classmethod
    def from_element(
        cls,
        el: _Identifiable,
        *,
        window_id: int,
        app_pid: int,
        generation: int,
        native_identity: str = "",
    ) -> AXTargetRef:
        raw = getattr(el, "bounds", (0, 0, 0, 0))
        bounds = tuple(raw) if isinstance(raw, tuple) and len(raw) == 4 else (0, 0, 0, 0)
        return cls(
            window_id=window_id,
            app_pid=app_pid,
            generation=generation,
            role=getattr(el, "role", ""),
            name=getattr(el, "name", ""),
            automation_id=getattr(el, "automation_id", ""),
            native_identity=native_identity,
            bounds=bounds,  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class TargetResolution:
    verdict: str
    reason: str = ""
    element: _Identifiable | None = None  # the resolved current element when VALID

    @property
    def ok(self) -> bool:
        return self.verdict == VALID


IdentityOf = Callable[[_Identifiable], str]


def resolve(
    ref: AXTargetRef,
    snapshot: Sequence[_Identifiable],
    *,
    current_generation: int,
    identity_of: IdentityOf | None = None,
    move_tolerance_px: int = 4,
) -> TargetResolution:
    """Re-resolve ``ref`` against the current ``snapshot``, refusing anything
    that is not the same element at the same generation.

    Order: generation gate first (a stale ref is refused *without searching*);
    then exact identity + uniqueness; a native-identity token, when supplied,
    can rescue an otherwise-ambiguous match and must agree with the unique hit;
    finally a bounds sanity check (a same-generation element should not have
    moved). Never falls back to a fuzzy title/role re-search."""
    stale = gen_gate(ref.generation, current_generation)
    if stale is not None:
        return TargetResolution(
            STALE, f"generation {ref.generation} != current {current_generation}"
        )

    verdict, el = unique_identity_match(
        snapshot, automation_id=ref.automation_id, name=ref.name, role=ref.role
    )
    if verdict != VALID:
        # A native-identity token can disambiguate an ambiguous attribute match.
        if verdict == AMBIGUOUS and identity_of is not None and ref.native_identity:
            exact = [e for e in snapshot if identity_of(e) == ref.native_identity]
            if len(exact) == 1:
                el, verdict = exact[0], VALID
            else:
                return TargetResolution(AMBIGUOUS, "native identity did not uniquely match")
        else:
            return TargetResolution(verdict, f"identity re-find -> {verdict}")

    assert el is not None
    # If a native token is available, the unique attribute hit must still be the
    # same native element -- guards against a lookalike sharing all attributes.
    if identity_of is not None and ref.native_identity and identity_of(el) != ref.native_identity:
        return TargetResolution(GONE, "native identity of the matched element differs")

    # Same generation should not have moved; a large shift means re-observe.
    if ref.bounds != (0, 0, 0, 0):
        el_bounds = getattr(el, "bounds", None)
        if isinstance(el_bounds, tuple) and len(el_bounds) == 4:
            rx, ry = _center(ref.bounds)
            ex, ey = _center(el_bounds)
            if abs(rx - ex) > move_tolerance_px or abs(ry - ey) > move_tolerance_px:
                return TargetResolution(MOVED, "matched identity but bounds moved", element=el)

    return TargetResolution(VALID, "", element=el)


def resolve_or_reject(
    ref: AXTargetRef,
    snapshot: Sequence[_Identifiable],
    *,
    current_generation: int,
    identity_of: IdentityOf | None = None,
    move_tolerance_px: int = 4,
) -> tuple[_Identifiable | None, TargetResolution]:
    """``resolve`` but returns ``(None, resolution)`` for any non-VALID verdict,
    so callers reject instead of guessing. VALID returns ``(element, resolution)``."""
    res = resolve(
        ref,
        snapshot,
        current_generation=current_generation,
        identity_of=identity_of,
        move_tolerance_px=move_tolerance_px,
    )
    return (res.element if res.ok else None, res)


__all__ = [
    "VALID",
    "STALE",
    "AMBIGUOUS",
    "GONE",
    "MOVED",
    "gen_gate",
    "unique_identity_match",
    "AXTargetRef",
    "TargetResolution",
    "resolve",
    "resolve_or_reject",
]
