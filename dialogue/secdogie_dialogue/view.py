"""One view surface over the structural state, with two modes the operator flips
between:

* **tree** -- the indented node list from :mod:`inspector` (role, name, DIB
  marks, focus, disabled);
* **geometry** -- the :mod:`wireframe` radar: the same nodes laid out in 2D by
  their bounds, so the operator sees *where* the Agent is working, not just the
  hierarchy.

Both are plain text lines from the same ``InspectorState`` -- no capture, no
pixels, no new dependency. The TUI keeps one :class:`InspectorView`, feeds it
each folded snapshot, and calls :meth:`lines`; a keypress calls :meth:`toggle`.
Pure and deterministic; unit-tested headless.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from .inspector import EMPTY, InspectorState, render_lines
from .wireframe import RADAR_OPTIONS, WireframeOptions, render_radar


class ViewMode(str, Enum):
    TREE = "tree"
    GEOMETRY = "geometry"

    @property
    def label(self) -> str:
        return "structural tree" if self is ViewMode.TREE else "geometry radar"


def render_view(
    state: InspectorState,
    mode: ViewMode = ViewMode.TREE,
    *,
    radar_options: WireframeOptions = RADAR_OPTIONS,
) -> list[str]:
    """The state's lines in ``mode``. Stateless; :class:`InspectorView` is the
    stateful wrapper the TUI holds."""
    if mode is ViewMode.GEOMETRY:
        return render_radar(state, radar_options)
    return render_lines(state)


@dataclass(frozen=True)
class InspectorView:
    """The current state and view mode, together. Immutable: every method
    returns a new view, so a TUI can keep history / undo for free."""

    state: InspectorState = EMPTY
    mode: ViewMode = ViewMode.TREE
    radar_options: WireframeOptions = field(default=RADAR_OPTIONS)

    def with_state(self, state: InspectorState) -> InspectorView:
        """A new view over ``state`` (already folded by ``inspector.apply``),
        keeping the current mode."""
        return replace(self, state=state)

    def toggle(self) -> InspectorView:
        other = ViewMode.GEOMETRY if self.mode is ViewMode.TREE else ViewMode.TREE
        return replace(self, mode=other)

    def with_mode(self, mode: ViewMode) -> InspectorView:
        return replace(self, mode=mode)

    def resized(self, width: int, height: int) -> InspectorView:
        """A new view whose geometry radar fills ``width`` x ``height`` cells.
        The tree view ignores it; the mode is unchanged."""
        opts = replace(self.radar_options, width=width, height=height)
        return replace(self, radar_options=opts)

    def lines(self) -> list[str]:
        return render_view(self.state, self.mode, radar_options=self.radar_options)

    def status(self) -> str:
        """One line for the mode indicator / key hint."""
        other = "geometry" if self.mode is ViewMode.TREE else "tree"
        flag = "⚠ resync" if self.state.needs_resync else "ok"
        return f"view: {self.mode.label}  [tab → {other}]  ({flag})"


__all__ = ["ViewMode", "InspectorView", "render_view"]
