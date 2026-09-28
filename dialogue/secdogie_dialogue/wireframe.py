"""Vector wireframe: the Agent's geometric view, drawn from structure alone.

The inspector shows the operator *what* the Agent perceives as a tree. This
module shows *where*: it lays the folded structural state out as a 2D wireframe
-- one rectangle per node, styled by semantic role and state, DIB-covered blind
regions hatched with their metadata, the Agent's focus on top -- and renders it
as a small SVG or a box-drawing "radar" for the terminal.

It is a pure function of an :class:`~secdogie_dialogue.inspector.InspectorState`
(the packets already folded and checked by :func:`inspector.apply`). Nothing is
captured and there are no pixels anywhere: a node is its ``bounds`` (x, y, w, h
in screen pixels), its role and its flags; a blind region is a ``DibRef``'s
size, format and hash. The SVG is built from a fixed set of elements -- no
``<image>``, no links, no scripts -- and every label, which comes from an
arbitrary application's UI text, is cleaned and escaped. Standard library only;
unit-tested headless.
"""
from __future__ import annotations

import html
import unicodedata
from dataclasses import dataclass
from enum import Enum

from .inspector import EMPTY, InspectorState, apply, clean, rows
from .protocol import DibRef, StateSnapshotPacket

Rect = tuple[int, int, int, int]  # (x, y, w, h)


class ShapeKind(str, Enum):
    FRAME = "frame"  # a structural container
    DISABLED = "disabled"
    INTERACTIVE = "interactive"
    DIB = "dib"  # a blind region: DibRef metadata only
    FOCUS = "focus"  # overlay on the node the Agent is about to act on


# Paint order: containers first, focus last so it is always visible.
_PAINT = {ShapeKind.FRAME: 0, ShapeKind.DISABLED: 1, ShapeKind.INTERACTIVE: 2, ShapeKind.DIB: 3, ShapeKind.FOCUS: 4}
# Which shapes survive a max_shapes cap first: what the operator must see.
_KEEP = {ShapeKind.FOCUS: 0, ShapeKind.DIB: 1, ShapeKind.INTERACTIVE: 2, ShapeKind.DISABLED: 3, ShapeKind.FRAME: 4}


@dataclass(frozen=True)
class Shape:
    index: int  # the node's handle (NodeView.index), to link with the tree view
    kind: ShapeKind
    rect: Rect  # viewport coordinates
    depth: int
    label: str  # cleaned, one line
    dib: DibRef | None = None


@dataclass(frozen=True)
class WireframeOptions:
    width: int = 960  # viewport width: pixels for SVG, character cells for the radar
    height: int = 600
    pad: int = 8
    cell_aspect: float = 1.0  # height/width of one viewport unit; ~2.0 for terminal cells
    max_shapes: int = 2000
    min_size: int = 2  # shapes smaller than this in either dimension are dropped

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0 or self.pad < 0 or 2 * self.pad >= min(self.width, self.height):
            raise ValueError("viewport must be positive and larger than twice the padding")
        if self.cell_aspect <= 0 or self.max_shapes < 0 or self.min_size < 1:
            raise ValueError("cell_aspect must be positive, max_shapes >= 0, min_size >= 1")


SVG_OPTIONS = WireframeOptions()
RADAR_OPTIONS = WireframeOptions(width=100, height=30, pad=0, cell_aspect=2.0, min_size=1)


@dataclass(frozen=True)
class Wireframe:
    viewport: tuple[int, int]
    world: Rect  # the screen area mapped into the viewport
    scale: float  # viewport units per screen pixel, horizontally
    offset: tuple[float, float]
    cell_aspect: float
    shapes: tuple[Shape, ...]  # in paint order
    stale: bool  # state.needs_resync: this is the last consistent tree
    problem: str
    dropped_degenerate: int = 0
    dropped_overflow: int = 0
    clipped: int = 0

    def to_world(self, vx: float, vy: float) -> tuple[float, float]:
        """Viewport point -> screen point (inverse of the layout transform)."""
        if self.scale <= 0:
            return (float(self.world[0]), float(self.world[1]))
        x = self.world[0] + (vx - self.offset[0]) / self.scale
        y = self.world[1] + (vy - self.offset[1]) * self.cell_aspect / self.scale
        return (x, y)


def _valid(b: tuple[int, int, int, int]) -> bool:
    return b[2] > 0 and b[3] > 0


def _union(boxes) -> Rect | None:
    boxes = [b for b in boxes if _valid(b)]
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def _node_label(role: str, name: str) -> str:
    return clean(f'{role} "{name}"' if name else role)


def _dib_label(ref: DibRef) -> str:
    return clean(f"DIB {ref.width}x{ref.height} {ref.pixel_format} #{ref.content_hash[:8]}")


class VectorWireframeEngine:
    """Lays an ``InspectorState`` out as a :class:`Wireframe`."""

    def __init__(self, options: WireframeOptions = SVG_OPTIONS):
        self.options = options

    def from_packet(self, pkt: StateSnapshotPacket) -> Wireframe:
        """Convenience for a *full* snapshot. A delta only lists what changed,
        so it has nothing to draw on its own: fold it with ``inspector.apply``
        and call :meth:`build` instead."""
        if not pkt.full:
            raise ValueError("from_packet needs a full snapshot; fold deltas with inspector.apply()")
        return self.build(apply(EMPTY, pkt))

    def build(self, state: InspectorState) -> Wireframe:
        o = self.options
        order = rows(state)  # depth-first, the same order the tree view shows
        roots = [state.nodes[r.index].bounds for r in order if state.nodes[r.index].parent_index == -1]
        world = _union(roots) or _union(n.bounds for n in state.nodes.values())
        if world is None:
            return Wireframe((o.width, o.height), (0, 0, 0, 0), 0.0, (0.0, 0.0), o.cell_aspect, (),
                             state.needs_resync, state.problem or "nothing with a size to draw",
                             dropped_degenerate=len(state.nodes))

        wx, wy, ww, wh = world
        avail_w, avail_h = o.width - 2 * o.pad, o.height - 2 * o.pad
        scale = min(avail_w / ww, avail_h * o.cell_aspect / wh)
        used_w, used_h = ww * scale, wh * scale / o.cell_aspect
        off = ((o.width - used_w) / 2, (o.height - used_h) / 2)

        def to_view(b) -> tuple[Rect | None, bool]:
            x0 = off[0] + (b[0] - wx) * scale
            y0 = off[1] + (b[1] - wy) * scale / o.cell_aspect
            x1 = off[0] + (b[0] + b[2] - wx) * scale
            y1 = off[1] + (b[1] + b[3] - wy) * scale / o.cell_aspect
            rx0, ry0, rx1, ry1 = round(x0), round(y0), round(x1), round(y1)
            cx0, cy0 = max(0, rx0), max(0, ry0)
            cx1, cy1 = min(o.width, rx1), min(o.height, ry1)
            clipped = (cx0, cy0, cx1, cy1) != (rx0, ry0, rx1, ry1)
            if cx1 - cx0 < o.min_size or cy1 - cy0 < o.min_size:
                return None, clipped
            return (cx0, cy0, cx1 - cx0, cy1 - cy0), clipped

        candidates: list[tuple[int, Shape]] = []  # (dfs position, shape)
        degenerate = clipped = 0
        for pos, r in enumerate(order):
            n = state.nodes[r.index]
            if not _valid(n.bounds):
                degenerate += 1
                continue
            rect, was_clipped = to_view(n.bounds)
            if rect is None:
                degenerate += 1
                continue
            clipped += was_clipped
            ref = state.dib.get(n.index)
            if ref is not None:
                kind, label = ShapeKind.DIB, _dib_label(ref)
            elif not n.enabled:
                kind, label = ShapeKind.DISABLED, _node_label(n.role, n.name)
            elif n.is_interactive:
                kind, label = ShapeKind.INTERACTIVE, _node_label(n.role, n.name)
            else:
                kind, label = ShapeKind.FRAME, _node_label(n.role, n.name)
            candidates.append((pos, Shape(n.index, kind, rect, r.depth, label, ref)))
            if n.index == state.focused:
                candidates.append((pos, Shape(n.index, ShapeKind.FOCUS, rect, r.depth,
                                              _node_label(n.role, n.name), ref)))

        kept = sorted(candidates, key=lambda c: (_KEEP[c[1].kind], c[0]))[: o.max_shapes]
        overflow = len(candidates) - len(kept)
        kept.sort(key=lambda c: (_PAINT[c[1].kind], c[0]))
        return Wireframe((o.width, o.height), world, scale, off, o.cell_aspect,
                         tuple(s for _p, s in kept), state.needs_resync, state.problem,
                         dropped_degenerate=degenerate, dropped_overflow=overflow, clipped=clipped)


# ---- SVG -------------------------------------------------------------------------

_SVG_STYLE = {
    ShapeKind.FRAME: 'fill="none" stroke="#6b7280" stroke-width="1"',
    ShapeKind.DISABLED: 'fill="none" stroke="#9ca3af" stroke-width="1" stroke-dasharray="3 3"',
    ShapeKind.INTERACTIVE: 'fill="#2563eb" fill-opacity="0.08" stroke="#2563eb" stroke-width="1.5"',
    ShapeKind.DIB: 'fill="url(#dib-hatch)" stroke="#b45309" stroke-width="1.5"',
    ShapeKind.FOCUS: 'fill="none" stroke="#dc2626" stroke-width="3"',
}
_SVG_TEXT = {
    ShapeKind.FRAME: "#374151",
    ShapeKind.DISABLED: "#9ca3af",
    ShapeKind.INTERACTIVE: "#1d4ed8",
    ShapeKind.DIB: "#92400e",
    ShapeKind.FOCUS: "#b91c1c",
}
_CHAR_W, _LINE_H = 6.5, 12  # approximate glyph box at font-size 11


def to_svg(wf: Wireframe) -> str:
    """A standalone SVG: rectangles, hatch pattern, text. Nothing else."""
    w, h = wf.viewport
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        'font-family="monospace" font-size="11">',
        '<defs><pattern id="dib-hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="#d97706" '
        'stroke-width="1.5"/></pattern></defs>',
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>',
    ]
    for s in wf.shapes:
        x, y, sw, sh = s.rect
        out.append(f'<rect data-index="{s.index}" data-kind="{s.kind.value}" x="{x}" y="{y}" '
                   f'width="{sw}" height="{sh}" {_SVG_STYLE[s.kind]}/>')
        if s.kind is ShapeKind.FOCUS:
            continue  # the node's own shape already carries its label
        fit = int((sw - 4) // _CHAR_W)
        if fit >= 3 and sh >= _LINE_H + 2:
            label = s.label if len(s.label) <= fit else s.label[: fit - 1] + "…"
            out.append(f'<text x="{x + 2}" y="{y + _LINE_H - 1}" fill="{_SVG_TEXT[s.kind]}">'
                       f"{html.escape(label, quote=True)}</text>")
    if wf.stale:
        msg = html.escape(clean(f"⚠ resync needed: {wf.problem}"), quote=True)
        out.append(f'<text x="4" y="{h - 4}" fill="#b91c1c">{msg}</text>')
    out.append("</svg>")
    return "\n".join(out)


# ---- terminal radar ------------------------------------------------------------

_BOX = {
    ShapeKind.FRAME: "┌┐└┘─│",
    ShapeKind.DISABLED: "┌┐└┘┄┆",
    ShapeKind.INTERACTIVE: "┏┓┗┛━┃",
    ShapeKind.DIB: "┌┐└┘─│",
    ShapeKind.FOCUS: "╔╗╚╝═║",
}
DIB_FILL = "░"
_SMALL = "▪"
_WIDE = ""  # the second cell of a double-width character


def _cell_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class _Canvas:
    """A grid of cells where a double-width character owns two cells, so every
    rendered line is exactly ``width`` display columns."""

    def __init__(self, width: int, height: int):
        self.w, self.h = width, height
        self.cells = [[" "] * width for _ in range(height)]

    def put(self, x: int, y: int, ch: str) -> int:
        """Write ``ch`` at (x, y); returns the columns used (0 if it doesn't fit)."""
        cw = _cell_width(ch)
        if not (0 <= y < self.h and 0 <= x and x + cw <= self.w):
            return 0
        row = self.cells[y]
        for i in range(x, x + cw):  # break any wide character we overwrite half of
            if row[i] == _WIDE and i - 1 >= 0:
                row[i - 1] = " "
            if i + 1 < self.w and row[i + 1] == _WIDE:
                row[i + 1] = " "
        row[x] = ch
        if cw == 2:
            row[x + 1] = _WIDE
        return cw

    def text(self, x: int, y: int, s: str, limit: int) -> None:
        used = 0
        for ch in s:
            if used + _cell_width(ch) > limit:
                break
            used += self.put(x + used, y, ch) or _cell_width(ch)

    def lines(self) -> list[str]:
        return ["".join(row) for row in self.cells]


def to_text_grid(wf: Wireframe) -> list[str]:
    """The wireframe as ``height`` lines of ``width`` display columns: light
    boxes for structure, heavy for interactive, dashed for disabled, ``░`` for
    blind (DIB) regions, a double box for the focus. Three passes -- borders,
    then labels on each box's top edge, then the focus -- so a later border
    never erases a label and the focus is always visible."""
    w, h = wf.viewport
    c = _Canvas(w, h)
    body = [s for s in wf.shapes if s.kind is not ShapeKind.FOCUS]
    for s in body:
        _box(c, s)
    for s in body:
        x, y, sw, sh = s.rect
        if sw >= 3:
            hz = _BOX[s.kind][4]
            for xx in range(x + 1, x + sw - 1):  # clear an earlier label sharing this edge
                c.put(xx, y, hz)
            c.text(x + 1, y, s.label, sw - 2)
    for s in wf.shapes:
        if s.kind is ShapeKind.FOCUS:
            _box(c, s)
    return c.lines()


def _box(c: _Canvas, s: Shape) -> None:
    x, y, sw, sh = s.rect
    x1, y1 = x + sw - 1, y + sh - 1
    focus = s.kind is ShapeKind.FOCUS
    if sh < 2 and sw >= 3 and focus:  # mark the ends; keep the node's label readable
        c.put(x, y, "«")
        c.put(x1, y, "»")
        return
    if sh < 2 and sw >= 3:  # one row tall: a bracketed strip, labelled in pass two
        c.put(x, y, "[")
        for xx in range(x + 1, x1):
            c.put(xx, y, _BOX[s.kind][4])
        c.put(x1, y, "]")
        return
    if sw < 2 or sh < 2:
        c.put(x, y, _SMALL)
        return
    tl, tr, bl, br, hz, vt = _BOX[s.kind]
    if s.kind is ShapeKind.DIB:
        for yy in range(y + 1, y1):
            for xx in range(x + 1, x1):
                c.put(xx, yy, DIB_FILL)
    for xx in range(x + 1, x1):
        if not focus:  # the focus keeps the top edge's label visible
            c.put(xx, y, hz)
        c.put(xx, y1, hz)
    for yy in range(y + 1, y1):
        c.put(x, yy, vt)
        c.put(x1, yy, vt)
    c.put(x, y, tl)
    c.put(x1, y, tr)
    c.put(x, y1, bl)
    c.put(x1, y1, br)


def render_radar(state: InspectorState, options: WireframeOptions = RADAR_OPTIONS) -> list[str]:
    """Header line (the inspector's, with any resync warning) + the radar."""
    from .inspector import header_line

    return [header_line(state), *to_text_grid(VectorWireframeEngine(options).build(state))]


__all__ = [
    "ShapeKind",
    "Shape",
    "WireframeOptions",
    "SVG_OPTIONS",
    "RADAR_OPTIONS",
    "Wireframe",
    "VectorWireframeEngine",
    "to_svg",
    "to_text_grid",
    "render_radar",
    "DIB_FILL",
]
