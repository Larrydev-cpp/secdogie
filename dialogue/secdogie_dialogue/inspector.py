"""Zero-Screenshot Inspector: the structural view the operator watches.

The Agent streams ``StateSnapshotPacket``s -- a full tree, then deltas -- built
from the AX / UIA tree plus DIB *metadata*. This module folds them into one
immutable ``InspectorState`` and turns that into rows for display. It does no
perception of its own: no screen capture, no process access, nothing but the
packets it is given. There are no pixels anywhere in the model, only a DIB
region's size, format and content hash.

Folding is strict. A delta is applied only if it applies cleanly to what we
already hold; anything inconsistent -- a delta built on a generation other than
the one we hold (one in between was lost), an update of an unknown node, a
parent that does not exist, a cycle, a focus or DIB reference to nothing, a
delta for a window we have no base for -- leaves the last consistent tree in
place and marks the state ``needs_resync``. The App then asks for a full snapshot rather than
showing a guess. Snapshots at or below the current generation are stale and
ignored.

Pure functions over frozen data; unit-tested headless.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, replace

from .protocol import DibRef, NodeDelta, NodeOp, StateSnapshotPacket

MAX_LABEL = 120


@dataclass(frozen=True)
class NodeView:
    index: int
    parent_index: int
    role: str
    name: str
    automation_id: str = ""
    path_index: tuple[int, ...] = ()
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    enabled: bool = True
    is_interactive: bool = False

    @classmethod
    def of(cls, d: NodeDelta) -> NodeView:
        return cls(d.index, d.parent_index, d.role, d.name, d.automation_id, d.path_index,
                   d.bounds, d.enabled, d.is_interactive)


@dataclass(frozen=True)
class InspectorState:
    """What the operator sees. ``nodes`` / ``dib`` are never mutated after
    construction; ``apply`` builds new ones."""

    window_id: int = -1
    app_pid: int = 0
    generation: int = -1
    nodes: dict[int, NodeView] = field(default_factory=dict)  # insertion order = display order
    dib: dict[int, DibRef] = field(default_factory=dict)
    focused: int = -1
    needs_resync: bool = True  # nothing yet: the first thing we need is a full snapshot
    problem: str = "no snapshot yet"


EMPTY = InspectorState()


class _Inconsistent(Exception):
    pass


def apply(state: InspectorState, pkt: StateSnapshotPacket) -> InspectorState:
    """Fold one snapshot into ``state``. Never raises for bad input: an
    inconsistent packet yields the previous tree marked ``needs_resync``."""
    same_window = pkt.window_id == state.window_id
    if same_window and pkt.generation <= state.generation:
        return state  # stale or duplicate
    if not pkt.full:
        if state.needs_resync:
            return state  # no consistent base to apply a delta to; still waiting for a full snapshot
        if not same_window:
            return _desync(state, "a delta for a window we hold no tree for")
        if pkt.base_generation != state.generation:
            # built on a tree we never saw (a delta in between was lost)
            return _desync(state, f"gap: delta applies to generation {pkt.base_generation}, "
                                  f"holding {state.generation}")
    try:
        if pkt.full:
            nodes, dib = _rebuild(pkt.nodes)
        else:
            nodes, dib = _patch(state.nodes, state.dib, pkt.nodes)
        _check_tree(nodes)
        for ref in pkt.dib_references:
            if ref.node_index not in nodes:
                raise _Inconsistent(f"DIB reference to unknown node {ref.node_index}")
            dib[ref.node_index] = ref
        if pkt.focused_node_index != -1 and pkt.focused_node_index not in nodes:
            raise _Inconsistent(f"focus on unknown node {pkt.focused_node_index}")
    except _Inconsistent as e:
        return _desync(state, str(e))
    return InspectorState(pkt.window_id, pkt.app_pid, pkt.generation, nodes, dib,
                          pkt.focused_node_index, needs_resync=False, problem="")


def _desync(state: InspectorState, why: str) -> InspectorState:
    return replace(state, needs_resync=True, problem=why)


def _rebuild(deltas: tuple[NodeDelta, ...]):
    nodes: dict[int, NodeView] = {}
    for d in deltas:
        if d.index in nodes:
            raise _Inconsistent(f"node {d.index} listed twice in a full snapshot")
        nodes[d.index] = NodeView.of(d)
    return nodes, {}


def _patch(old_nodes, old_dib, deltas: tuple[NodeDelta, ...]):
    nodes = dict(old_nodes)
    dib = dict(old_dib)
    for d in deltas:
        if d.op is NodeOp.ADD:
            if d.index in nodes:
                raise _Inconsistent(f"add of existing node {d.index}")
            nodes[d.index] = NodeView.of(d)
        elif d.op is NodeOp.UPDATE:
            if d.index not in nodes:
                raise _Inconsistent(f"update of unknown node {d.index}")
            nodes[d.index] = NodeView.of(d)
        else:  # REMOVE: the node and its whole subtree
            if d.index not in nodes:
                raise _Inconsistent(f"remove of unknown node {d.index}")
            for gone in _subtree(nodes, d.index):
                del nodes[gone]
                dib.pop(gone, None)
    return nodes, dib


def _subtree(nodes: dict[int, NodeView], root: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for n in nodes.values():
        children.setdefault(n.parent_index, []).append(n.index)
    out, stack = [], [root]
    while stack:
        i = stack.pop()
        out.append(i)
        stack.extend(children.get(i, ()))
    return out


def _check_tree(nodes: dict[int, NodeView]) -> None:
    """Every parent exists (or is -1) and every node reaches a root.

    The walk up from each node is bounded by construction: a path to a root
    visits at most ``len(nodes)`` distinct nodes, so a walk that has not reached
    one after ``len(nodes) + 1`` steps has revisited a node -- a cycle. Nothing
    here can loop forever on hostile input."""
    ok: set[int] = set()  # nodes already known to reach a root
    for start in nodes:
        path: list[int] = []
        i = start
        for _ in range(len(nodes) + 1):
            if i == -1 or i in ok:
                break
            if i not in nodes:
                raise _Inconsistent(f"node {path[-1]} has unknown parent {i}")
            path.append(i)
            i = nodes[i].parent_index
        else:
            raise _Inconsistent(f"cycle through node {start}")
        ok.update(path)


# ---- render model ------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    depth: int
    index: int
    label: str  # one line, control characters replaced, length-capped
    focused: bool
    enabled: bool
    dib: DibRef | None


def clean(text: str, limit: int = MAX_LABEL) -> str:
    """One display line: control characters become spaces, then cap the length.
    Node names come from arbitrary applications' UI text."""
    out = "".join(" " if unicodedata.category(c).startswith("C") else c for c in text)
    return out if len(out) <= limit else out[: limit - 1] + "…"


def rows(state: InspectorState) -> list[Row]:
    """Depth-first, children in the order they were first seen."""
    children: dict[int, list[int]] = {}
    for n in state.nodes.values():
        children.setdefault(n.parent_index, []).append(n.index)
    out: list[Row] = []
    stack = [(i, 0) for i in reversed(children.get(-1, []))]
    while stack:
        i, depth = stack.pop()
        n = state.nodes[i]
        label = clean(f'{n.role} "{n.name}"' if n.name else n.role)
        out.append(Row(depth, i, label, i == state.focused, n.enabled, state.dib.get(i)))
        stack.extend((c, depth + 1) for c in reversed(children.get(i, [])))
    return out


def header_line(state: InspectorState) -> str:
    if state.generation < 0:
        return "(waiting for the first snapshot)"
    line = f"window#{state.window_id}  pid {state.app_pid}  gen {state.generation}"
    focus = f"  focus={state.focused}" if state.focused != -1 else ""
    warn = f"  ⚠ resync needed: {clean(state.problem)}" if state.needs_resync else ""
    return line + focus + warn


def render_lines(state: InspectorState) -> list[str]:
    """Plain-text view: the secdogie window's 视界 fold shows these lines; tests read them."""
    lines = [header_line(state)]
    for r in rows(state):
        marks = ""
        if r.dib is not None:
            marks += f"  [DIB {r.dib.width}x{r.dib.height} {clean(r.dib.pixel_format, 16)} #{clean(r.dib.content_hash[:8])}]"
        if not r.enabled:
            marks += "  (disabled)"
        if r.focused:
            marks += "  ★"
        lines.append("  " * r.depth + "▸ " + r.label + marks)
    return lines


__all__ = ["NodeView", "InspectorState", "EMPTY", "apply", "Row", "rows", "clean", "header_line",
           "render_lines", "MAX_LABEL"]
