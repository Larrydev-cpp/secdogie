"""Structural Adapter: turn an AX snapshot into a typed, tree-shaped ``Observation``.

The OS-facing half -- walking the live UIA / AT-SPI / AX tree into
``AxElement``s -- lives behind the ``DesktopAxProvider`` seam in desktop_ax.py.
This module is the other half: a pure in-memory conversion from that flat,
depth-first element list into the ``Observation`` that fusion (observation.py)
consumes. No image libraries, no OS calls, no capture; every sense is injected,
so the whole pipeline runs headless on CI against fakes.

Pipeline, in order:

1. **Prune.** ``FilterPolicy.drop_placeholders`` removes pure placeholders:
   nodes with no valid box AND no name AND no automation id. Nothing can target
   them and they only add noise. A pruned node's children re-attach to its
   nearest kept ancestor; nothing below it is lost.
2. **Recover the tree.** Providers emit elements in depth-first pre-order and
   record each one's walk depth (``AxElement.depth``). A node's parent is the
   nearest preceding kept node with a smaller walk depth. If any element has no
   recorded depth (-1: a fake or an older provider), the whole snapshot falls
   back to *inferring* nesting from box containment in pre-order. That is a
   heuristic, used only when the walk depth is missing. Every node then gets
   ``depth`` (0 = root, in the kept tree), ``path_index`` (child ordinals from
   its root) and ``parent_index`` (into ``semantic_nodes``, -1 = root).
3. **Classify.** ``is_interactive`` is set from the node's role against a
   cross-platform role table (UIA, AT-SPI and macOS AX spellings).
4. **Stitch DIB.** For custom-drawn blind spots, which AX cannot see into,
   an injected
   ``dib_provider`` is asked for a ``VisualReference``: a *handle* to a
   read-only reconstructed bitmap, never pixels. It is attached to that node.
   The adapter never reads memory itself. DIB is a secondary, verifying sense,
   so a failed or over-budget DIB read leaves the node without a reference and
   is recorded in ``last_report``. It never loses the AX reading.

Bounds map losslessly: (left, top, right, bottom) -> ``Geometry(x, y, w, h)``
with no clamping, so degenerate boxes keep their raw numbers. The window
``geometry`` is the union of the kept nodes' valid boxes.

A ``None`` or empty snapshot (or one pruned to nothing) yields a standard empty
observation: no nodes, no geometry, confidence 0.0. That means "perceived
nothing", never an invented reading. Errors from the AX provider itself propagate:
a failed tree read is not the same thing as an empty window.
"""
from __future__ import annotations

import dataclasses
import fnmatch
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..axtree import AxElement
from ..desktop_ax import DesktopAxProvider
from ..observation import Budget, Geometry, Observation, SemanticNode, VisualReference, observe_ax

# Confidence of a populated AX reading; matches observe_ax's default so an
# adapter-built observation fuses exactly like a hand-built one.
DEFAULT_AX_CONFIDENCE = 0.9
EMPTY_CONFIDENCE = 0.0

SnapshotFn = Callable[[], "Sequence[AxElement] | None"]


def normalize_role(role: str) -> str:
    """Case-, space-, dash- and underscore-insensitive role key, so UIA
    ``CheckBox``, AT-SPI ``check box`` and macOS ``CheckBox`` all compare equal."""
    return re.sub(r"[\s_\-]+", "", role).casefold()


# Roles a user can click, type into, toggle or pick, as each platform spells them
# (UIA ControlTypeName minus "Control"; AT-SPI getRoleName(); macOS AXRole minus "AX").
DEFAULT_INTERACTIVE_ROLES: frozenset[str] = frozenset(
    normalize_role(r)
    for r in (
        # UIA
        "Button", "SplitButton", "Edit", "MenuItem", "CheckBox", "RadioButton",
        "ComboBox", "Hyperlink", "ListItem", "TabItem", "TreeItem", "Slider",
        "Spinner", "DataItem", "ScrollBar", "Thumb",
        # AT-SPI
        "push button", "toggle button", "check box", "radio button", "combo box",
        "menu item", "check menu item", "radio menu item", "text", "entry",
        "password text", "spin button", "slider", "link", "list item",
        "page tab", "tree item", "table cell",
        # macOS AX
        "PopUpButton", "MenuButton", "MenuBarItem", "TextField", "TextArea",
        "SearchField", "Link", "Incrementor", "Stepper", "DisclosureTriangle",
        "Cell", "Row",
    )
)  # fmt: skip

# Custom-drawn surfaces whose content the accessibility tree cannot describe.
# Always a blind spot, labelled or not: AX may name a canvas but never its content.
DEFAULT_BLIND_ROLES: frozenset[str] = frozenset(
    normalize_role(r) for r in ("Canvas", "RenderSurface", "Custom", "drawing area", "Unknown")
)

# Generic containers that are a blind spot only when they are *opaque leaves*:
# no children, no name, no automation id, but a real box. That is the shape of a
# game / video / GPU render window hosted in an otherwise accessible app.
DEFAULT_OPAQUE_LEAF_ROLES: frozenset[str] = frozenset(
    normalize_role(r) for r in ("Pane", "Group", "panel", "filler", "ScrollArea")
)


@dataclass(frozen=True)
class FilterPolicy:
    """What the adapter prunes and how it classifies. Role sets hold
    ``normalize_role`` keys; build custom ones with ``normalize_role``."""

    drop_placeholders: bool = True
    interactive_roles: frozenset[str] = DEFAULT_INTERACTIVE_ROLES
    blind_roles: frozenset[str] = DEFAULT_BLIND_ROLES
    opaque_leaf_roles: frozenset[str] = DEFAULT_OPAQUE_LEAF_ROLES
    dib_for_opaque_leaves: bool = True

    def is_placeholder(self, el: AxElement) -> bool:
        return not geometry_from_bounds(el.bounds).valid and not el.name and not el.automation_id

    def is_interactive(self, role: str) -> bool:
        return normalize_role(role) in self.interactive_roles

    def is_blind(self, node: SemanticNode, *, has_children: bool) -> bool:
        """Does AX lack the content of this node, so DIB should be consulted?"""
        if not node.bounds.valid:
            return False  # nothing on screen to reference
        role = normalize_role(node.role)
        if role in self.blind_roles:
            return True
        return (
            self.dib_for_opaque_leaves
            and role in self.opaque_leaf_roles
            and not has_children
            and not (node.name or node.automation_id)
        )


DEFAULT_FILTER_POLICY = FilterPolicy()
NO_PRUNING = FilterPolicy(drop_placeholders=False)


@runtime_checkable
class DibProvider(Protocol):
    def inspect(self, node: SemanticNode) -> VisualReference | None:
        """A read-only handle to the bitmap drawn in ``node.bounds``, or None if
        there isn't one. Returns a reference (identity + hash), never pixels."""
        ...


DibFn = Callable[[SemanticNode], "VisualReference | None"]


@dataclass(frozen=True)
class AdapterReport:
    """What the last ``get_observation`` did, so pruning and DIB gaps are
    visible instead of silent."""

    raw_count: int = 0
    kept_count: int = 0
    pruned_count: int = 0
    depth_inferred: bool = False
    dib_requested: int = 0
    dib_attached: int = 0
    dib_bytes: int = 0
    dib_skipped_budget: int = 0
    dib_errors: tuple[str, ...] = ()


class BaseObservationAdapter(ABC):
    """One sense, packaged: anything that can produce an ``Observation``."""

    @abstractmethod
    def get_observation(self) -> Observation:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Pure conversion helpers
# ---------------------------------------------------------------------------


def geometry_from_bounds(bounds: tuple[int, int, int, int]) -> Geometry:
    """(left, top, right, bottom) -> Geometry(x, y, w, h), no clamping."""
    left, top, right, bottom = bounds
    return Geometry(x=left, y=top, w=right - left, h=bottom - top)


def semantic_node_from(el: AxElement) -> SemanticNode:
    """Flat identity + bounds only; structure is filled in by ``build_nodes``."""
    return SemanticNode(
        role=el.role,
        name=el.name,
        automation_id=el.automation_id,
        bounds=geometry_from_bounds(el.bounds),
    )


def union_geometry(nodes: Iterable[SemanticNode]) -> Geometry:
    """Smallest rect covering every node with valid bounds; empty if none."""
    boxes = [n.bounds for n in nodes if n.bounds.valid]
    if not boxes:
        return Geometry()
    x0 = min(g.x for g in boxes)
    y0 = min(g.y for g in boxes)
    x1 = max(g.x + g.w for g in boxes)
    y1 = max(g.y + g.h for g in boxes)
    return Geometry(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def _contains(outer: Geometry, inner: Geometry) -> bool:
    return (
        outer.x <= inner.x
        and outer.y <= inner.y
        and outer.x + outer.w >= inner.x + inner.w
        and outer.y + outer.h >= inner.y + inner.h
    )


def _parents_from_depth(elements: Sequence[AxElement]) -> list[int]:
    """Parent of each element = nearest preceding element with a smaller walk
    depth (exact for a depth-first pre-order walk, including gaps left by nodes
    the provider or pruning dropped)."""
    parents: list[int] = []
    stack: list[int] = []
    for i, el in enumerate(elements):
        while stack and elements[stack[-1]].depth >= el.depth:
            stack.pop()
        parents.append(stack[-1] if stack else -1)
        stack.append(i)
    return parents


def _parents_from_containment(elements: Sequence[AxElement]) -> list[int]:
    """Fallback when walk depth is missing: in pre-order, a node's parent is the
    nearest open ancestor whose box contains it. Box-less nodes attach to the
    current ancestor but can't contain anything themselves."""
    boxes = [geometry_from_bounds(el.bounds) for el in elements]
    parents: list[int] = []
    stack: list[int] = []
    for i, box in enumerate(boxes):
        if not box.valid:
            parents.append(stack[-1] if stack else -1)
            continue
        while stack and not _contains(boxes[stack[-1]], box):
            stack.pop()
        parents.append(stack[-1] if stack else -1)
        stack.append(i)
    return parents


def build_nodes(
    elements: Sequence[AxElement], policy: FilterPolicy = DEFAULT_FILTER_POLICY
) -> tuple[tuple[SemanticNode, ...], int, bool]:
    """Prune, recover the tree and classify. Returns
    ``(nodes, pruned_count, depth_inferred)``; DIB stitching happens later."""
    kept = [el for el in elements if not (policy.drop_placeholders and policy.is_placeholder(el))]
    pruned = len(elements) - len(kept)
    depth_inferred = any(el.depth < 0 for el in kept)
    parents = _parents_from_containment(kept) if depth_inferred else _parents_from_depth(kept)

    nodes: list[SemanticNode] = []
    child_counts: dict[int, int] = {}
    for i, el in enumerate(kept):
        parent = parents[i]
        ordinal = child_counts.get(parent, 0)
        child_counts[parent] = ordinal + 1
        path = (*nodes[parent].path_index, ordinal) if parent >= 0 else (ordinal,)
        nodes.append(
            dataclasses.replace(
                semantic_node_from(el),
                depth=len(path) - 1,
                path_index=path,
                parent_index=parent,
                is_interactive=policy.is_interactive(el.role),
            )
        )
    return tuple(nodes), pruned, depth_inferred


# ---------------------------------------------------------------------------
# Tree + query views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UiTreeNode:
    """One node of the recovered UI tree, with its position in ``semantic_nodes``."""

    index: int
    node: SemanticNode
    children: tuple[UiTreeNode, ...] = ()

    def walk(self) -> Iterator[UiTreeNode]:
        yield self
        for child in self.children:
            yield from child.walk()


def build_tree(nodes: Sequence[SemanticNode]) -> tuple[UiTreeNode, ...]:
    """Rebuild the nested tree from ``parent_index``; returns the roots in order."""
    children: dict[int, list[int]] = {}
    for i, n in enumerate(nodes):
        children.setdefault(n.parent_index, []).append(i)

    def make(i: int) -> UiTreeNode:
        return UiTreeNode(i, nodes[i], tuple(make(c) for c in children.get(i, ())))

    return tuple(make(r) for r in children.get(-1, ()))


def _matches(pattern: str | re.Pattern[str] | None, value: str) -> bool:
    if pattern is None:
        return True
    if isinstance(pattern, re.Pattern):
        return pattern.search(value) is not None
    return fnmatch.fnmatchcase(value.casefold(), pattern.casefold())


class NodeQuery:
    """Chainable, read-only query over an observation's nodes, in walk order.

    Filters return a new ``NodeQuery`` over the same universe, so tree
    navigation (``parent_of`` / ``children_of``) still works after narrowing:
    ``NodeQuery(obs).query_interactive().find_by_role_pattern("Button", "Save*")``."""

    def __init__(self, source: Observation | Sequence[SemanticNode], _indices: Sequence[int] | None = None):
        self._all: tuple[SemanticNode, ...] = tuple(
            source.semantic_nodes if isinstance(source, Observation) else source
        )
        self._indices: tuple[int, ...] = (
            tuple(range(len(self._all))) if _indices is None else tuple(_indices)
        )

    def _narrow(self, keep: Callable[[SemanticNode], bool]) -> NodeQuery:
        return NodeQuery(self._all, [i for i in self._indices if keep(self._all[i])])

    # -- filters ---------------------------------------------------------------

    def query_interactive(self) -> NodeQuery:
        return self._narrow(lambda n: n.is_interactive)

    def find_by_role_pattern(
        self,
        role: str | re.Pattern[str] | None = None,
        name_pattern: str | re.Pattern[str] | None = None,
    ) -> NodeQuery:
        """Match role and name. A ``str`` is a case-insensitive glob (``*``, ``?``,
        ``[...]``; no wildcard means exact); a compiled ``re.Pattern`` is
        searched as-is. ``None`` matches anything."""
        return self._narrow(lambda n: _matches(role, n.role) and _matches(name_pattern, n.name))

    def at_depth(self, depth: int) -> NodeQuery:
        return self._narrow(lambda n: n.depth == depth)

    def with_visual_reference(self) -> NodeQuery:
        return self._narrow(lambda n: n.visual_reference is not None)

    # -- lookups ---------------------------------------------------------------

    def find_by_automation_id(self, automation_id: str) -> SemanticNode | None:
        """First node (walk order) with this automation id, case-insensitive
        like ``axtree.find_elements``. An empty id never matches."""
        if not automation_id:
            return None
        want = automation_id.casefold()
        for i in self._indices:
            if self._all[i].automation_id.casefold() == want:
                return self._all[i]
        return None

    def parent_of(self, node: SemanticNode) -> SemanticNode | None:
        return self._all[node.parent_index] if node.parent_index >= 0 else None

    def children_of(self, node: SemanticNode) -> NodeQuery:
        idx = self._index_of(node)
        return NodeQuery(self._all, [i for i, n in enumerate(self._all) if n.parent_index == idx])

    def ancestors_of(self, node: SemanticNode) -> tuple[SemanticNode, ...]:
        """Nearest first, up to the root."""
        out = []
        cur = self.parent_of(node)
        while cur is not None:
            out.append(cur)
            cur = self.parent_of(cur)
        return tuple(out)

    def _index_of(self, node: SemanticNode) -> int:
        for i, n in enumerate(self._all):
            if n is node or n == node:
                return i
        raise ValueError("node is not part of this observation")

    # -- results ---------------------------------------------------------------

    def first(self) -> SemanticNode | None:
        return self._all[self._indices[0]] if self._indices else None

    def nodes(self) -> tuple[SemanticNode, ...]:
        return tuple(self._all[i] for i in self._indices)

    def indices(self) -> tuple[int, ...]:
        return self._indices

    def __iter__(self) -> Iterator[SemanticNode]:
        return iter(self.nodes())

    def __len__(self) -> int:
        return len(self._indices)

    def __bool__(self) -> bool:
        return bool(self._indices)


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


def _as_callable(provider, method: str, what: str):
    fn = getattr(provider, method, None)
    if callable(fn):
        return fn
    if callable(provider):
        return provider
    raise TypeError(f"{what} must have a {method}() method or be callable")


class StructuralObservationAdapter(BaseObservationAdapter):
    """Turn an AX provider's ``snapshot()`` into a tree-shaped AX ``Observation``.

    ``ax_provider`` is a ``DesktopAxProvider`` (anything with ``.snapshot()``)
    or a zero-argument callable returning ``list[AxElement] | None``.
    ``dib_provider`` is optional: a ``DibProvider`` (``.inspect(node)``) or a
    callable ``node -> VisualReference | None``. Window identity and generation
    come from the caller, because the AX snapshot does not carry them. Total DIB
    size per observation is capped at ``dib_budget_bytes``, which is the
    ``Budget.max_dib_bytes`` hard limit by default."""

    def __init__(
        self,
        ax_provider: DesktopAxProvider | SnapshotFn,
        *,
        window_id: int = 0,
        app_pid: int = 0,
        generation: int = 0,
        confidence: float = DEFAULT_AX_CONFIDENCE,
        clock: Callable[[], float] = time.time,
        filter_policy: FilterPolicy = DEFAULT_FILTER_POLICY,
        dib_provider: DibProvider | DibFn | None = None,
        dib_budget_bytes: int = Budget().max_dib_bytes,
    ) -> None:
        self._snapshot: SnapshotFn = _as_callable(ax_provider, "snapshot", "ax_provider")
        self._inspect: DibFn | None = (
            None if dib_provider is None else _as_callable(dib_provider, "inspect", "dib_provider")
        )
        self.window_id = window_id
        self.app_pid = app_pid
        self.generation = generation
        self.confidence = confidence
        self.filter_policy = filter_policy
        self.dib_budget_bytes = dib_budget_bytes
        self._clock = clock
        self.last_report = AdapterReport()

    def get_observation(self) -> Observation:
        elements = list(self._snapshot() or ())
        nodes, pruned, inferred = build_nodes(elements, self.filter_policy)
        nodes, dib = self._stitch_dib(nodes)
        self.last_report = AdapterReport(
            raw_count=len(elements),
            kept_count=len(nodes),
            pruned_count=pruned,
            depth_inferred=inferred and bool(nodes),
            **dib,
        )
        if not nodes:
            return self._empty_observation()
        return observe_ax(
            window_id=self.window_id,
            app_pid=self.app_pid,
            semantic_nodes=nodes,
            geometry=union_geometry(nodes),
            generation=self.generation,
            confidence=self.confidence,
            clock=self._clock,
        )

    def query(self) -> NodeQuery:
        """Take a fresh observation and return a query over its nodes."""
        return NodeQuery(self.get_observation())

    def _stitch_dib(self, nodes: tuple[SemanticNode, ...]) -> tuple[tuple[SemanticNode, ...], dict]:
        stats = {"dib_requested": 0, "dib_attached": 0, "dib_bytes": 0, "dib_skipped_budget": 0}
        if self._inspect is None:
            return nodes, stats
        errors: list[str] = []
        out = list(nodes)
        parents = {n.parent_index for n in nodes}
        for i, node in enumerate(nodes):
            if not self.filter_policy.is_blind(node, has_children=i in parents):
                continue
            stats["dib_requested"] += 1
            try:
                ref = self._inspect(node)
            except Exception as exc:  # secondary sense: record, keep the AX reading
                errors.append(f"{'.'.join(map(str, node.path_index))} {node.role}: {exc}")
                continue
            if ref is None:
                continue
            if not isinstance(ref, VisualReference):
                errors.append(f"{'.'.join(map(str, node.path_index))} {node.role}: not a VisualReference")
                continue
            if stats["dib_bytes"] + ref.approx_bytes > self.dib_budget_bytes:
                stats["dib_skipped_budget"] += 1
                continue
            stats["dib_bytes"] += ref.approx_bytes
            stats["dib_attached"] += 1
            out[i] = dataclasses.replace(node, visual_reference=ref)
        stats["dib_errors"] = tuple(errors)
        return tuple(out), stats

    def _empty_observation(self) -> Observation:
        return observe_ax(
            window_id=self.window_id,
            app_pid=self.app_pid,
            generation=self.generation,
            confidence=EMPTY_CONFIDENCE,
            clock=self._clock,
        )


__all__ = [
    "DEFAULT_AX_CONFIDENCE",
    "EMPTY_CONFIDENCE",
    "DEFAULT_INTERACTIVE_ROLES",
    "DEFAULT_BLIND_ROLES",
    "DEFAULT_OPAQUE_LEAF_ROLES",
    "DEFAULT_FILTER_POLICY",
    "NO_PRUNING",
    "AdapterReport",
    "BaseObservationAdapter",
    "DibProvider",
    "FilterPolicy",
    "NodeQuery",
    "StructuralObservationAdapter",
    "UiTreeNode",
    "build_nodes",
    "build_tree",
    "geometry_from_bounds",
    "normalize_role",
    "semantic_node_from",
    "union_geometry",
]
