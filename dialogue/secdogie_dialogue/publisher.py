"""Node side of the structural view: turn the loop's element list into
``StateSnapshotPacket``s -- a full tree, then deltas.

The node already has a structural view each step: the accessibility elements
the loop offers the model (role, name, automation id, bounds). This module only
*reshapes* that list for the operator's inspector. It perceives nothing: no
capture, no process access, no import of the Agent (the purity test enforces
it), and it reads exactly the attributes listed in ``FIELDS`` from each element
-- nothing else, so no pixel buffer can ride along. A DIB appears only as a
``DibRef`` (size, pixel format, content hash) when an element carries a
``visual_reference`` with those fields.

Elements are duck-typed:

  * ``role``, ``name``, ``automation_id`` -- strings (missing = "");
  * ``bounds`` -- ``(left, top, right, bottom)`` as the accessibility tree
    reports it, or an object with ``x`` / ``y`` / ``w`` / ``h``;
  * optional ``enabled``, ``is_interactive`` (bools) and ``visual_reference``.

The list is flat -- the loop offers the model a list of targets, not a tree --
so every element hangs under one synthetic window node. Each element keeps the
same handle across steps while its identity (role, name, automation id, and
its rank among identical elements) is unchanged, so the operator's view
changes by deltas, not flicker. Every delta
names the generation it applies to; ``request_full()`` (the App's RESYNC)
re-sends the current tree in full at once, even while the loop is waiting.
Thread-safe: the loop publishes while the session thread asks for resyncs.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .protocol import DibRef, NodeDelta, NodeOp, StateSnapshotPacket

FIELDS = ("role", "name", "automation_id", "bounds", "enabled", "is_interactive", "visual_reference")
DIB_FIELDS = ("width", "height", "pixel_format", "bit_count", "content_hash")
MAX_NODES = 2000
MAX_TEXT = 200
DEFAULT_FULL_EVERY = 50
ROOT = 0  # the synthetic window node every element hangs under

log = logging.getLogger("secdogie_dialogue.publisher")


def _get(obj, name, default):
    return getattr(obj, name, default) if name in FIELDS or name in DIB_FIELDS else default


def _text(v) -> str:
    return str(v or "")[:MAX_TEXT]


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _bounds(b) -> tuple[int, int, int, int]:
    if b is None:
        return (0, 0, 0, 0)
    if all(hasattr(b, k) for k in ("x", "y", "w", "h")):
        x, y, w, h = (_int(getattr(b, k)) for k in ("x", "y", "w", "h"))
    else:
        try:
            left, top, right, bottom = (_int(v) for v in b)
        except (TypeError, ValueError):
            return (0, 0, 0, 0)
        x, y, w, h = left, top, right - left, bottom - top
    return (x, y, max(0, w), max(0, h))


def _dib(ref, index: int) -> DibRef | None:
    if ref is None:
        return None
    digest = _text(_get(ref, "content_hash", ""))
    if not digest:
        return None
    fmt = _text(_get(ref, "pixel_format", "")) or (f"{_int(_get(ref, 'bit_count', 0))}bpp")
    return DibRef(index, max(0, _int(_get(ref, "width", 0))), max(0, _int(_get(ref, "height", 0))), fmt[:32],
                  digest[:128])


@dataclass(frozen=True)
class _Node:
    key: tuple
    role: str
    name: str
    automation_id: str
    bounds: tuple[int, int, int, int]
    enabled: bool
    is_interactive: bool
    dib: object


def _read(elements) -> list[_Node]:
    """Read the allowed fields of up to MAX_NODES elements."""
    nodes: list[_Node] = []
    seen: dict[tuple, int] = {}
    for el in elements:
        if len(nodes) >= MAX_NODES:
            break
        role, name, aid = _text(_get(el, "role", "")), _text(_get(el, "name", "")), _text(_get(el, "automation_id", ""))
        rank = seen.get((role, name, aid), 0)
        seen[(role, name, aid)] = rank + 1
        nodes.append(_Node((role, name, aid, rank), role, name, aid, _bounds(_get(el, "bounds", None)),
                           bool(_get(el, "enabled", True)), bool(_get(el, "is_interactive", True)),
                           _get(el, "visual_reference", None)))
    return nodes


class SnapshotPublisher:
    """``send(packet)`` delivers one snapshot (the session's unreliable
    channel). ``full_every`` re-sends the whole tree every so many packets, a
    safety net under the gap detection."""

    def __init__(self, send: Callable[[StateSnapshotPacket], object], *, window_id: int = 0, app_pid: int = 0,
                 full_every: int = DEFAULT_FULL_EVERY):
        self._send = send
        self._window = (int(window_id), int(app_pid))
        self._full_every = int(full_every)
        self._lock = threading.Lock()
        self._handles: dict[tuple, int] = {}  # identity key -> handle, for this window's stream
        self._next_handle = ROOT + 1
        self._tree: dict[int, NodeDelta] = {}  # handle -> the node as last published (ADD form)
        self._dib: dict[int, DibRef] = {}
        self._focused = -1
        self._generation = -1
        self._since_full = 0
        self._need_full = True

    def request_full(self) -> StateSnapshotPacket | None:
        """The App asked for a resync: send the current tree in full now."""
        with self._lock:
            self._need_full = True
            if self._generation < 0:
                return None  # nothing published yet; the first publish is full anyway
            pkt = self._packet(self._tree, [], self._dib, self._focused, full=True)
        return self._emit(pkt)

    def publish(self, elements: Iterable, *, window_id: int | None = None, app_pid: int | None = None,
                focused: int | None = None) -> StateSnapshotPacket | None:
        """Publish the current element list (``focused``: a position in it).
        Returns the packet sent, or None when nothing changed."""
        nodes = _read(elements)
        with self._lock:
            window = (self._window[0] if window_id is None else int(window_id),
                      self._window[1] if app_pid is None else int(app_pid))
            if window != self._window:
                self._window = window
                self._handles, self._next_handle = {}, ROOT + 1
                self._need_full = True
            tree, dib, handle_of = self._build(nodes)
            focus = handle_of[focused] if focused is not None and 0 <= focused < len(handle_of) else -1
            full = self._need_full or (self._full_every > 0 and self._since_full + 1 >= self._full_every)
            if full:
                pkt = self._packet(tree, [], dib, focus, full=True)
            else:
                changes = self._diff(tree)
                if not changes and dib == self._dib and focus == self._focused:
                    return None
                pkt = self._packet(tree, changes, dib, focus, full=False)
            self._tree, self._dib, self._focused = tree, dib, focus
        return self._emit(pkt)

    # -- internals (lock held) ------------------------------------------------------

    def _handle(self, key: tuple) -> int:
        h = self._handles.get(key)
        if h is None:
            h = self._handles[key] = self._next_handle
            self._next_handle += 1
        return h

    def _build(self, nodes: list[_Node]):
        tree: dict[int, NodeDelta] = {ROOT: NodeDelta(NodeOp.ADD, ROOT, role="window", name="")}
        dib: dict[int, DibRef] = {}
        handles = [self._handle(n.key) for n in nodes]
        for n, h in zip(nodes, handles, strict=True):
            tree[h] = NodeDelta(NodeOp.ADD, h, automation_id=n.automation_id, role=n.role, name=n.name,
                                bounds=n.bounds, enabled=n.enabled, is_interactive=n.is_interactive,
                                parent_index=ROOT)
            ref = _dib(n.dib, h)
            if ref is not None:
                dib[h] = ref
        return tree, dib, handles

    def _diff(self, tree: dict[int, NodeDelta]) -> list[NodeDelta]:
        old = self._tree
        changes = [d for h, d in tree.items() if h not in old]
        changes += [NodeDelta(NodeOp.UPDATE, h, d.automation_id, d.path_index, d.role, d.name, d.bounds, d.enabled,
                              d.is_interactive, d.parent_index)
                    for h, d in tree.items() if h in old and old[h] != d]
        changes += [NodeDelta(NodeOp.REMOVE, h) for h in old if h not in tree]  # leaves: no subtrees
        return changes

    def _packet(self, tree, changes, dib, focus, *, full: bool) -> StateSnapshotPacket:
        base = self._generation
        self._generation += 1
        window_id, app_pid = self._window
        if full:
            self._need_full, self._since_full = False, 0
            return StateSnapshotPacket(window_id, app_pid, self._generation, tuple(tree.values()),
                                       tuple(dib.values()), focus, full=True)
        self._since_full += 1
        return StateSnapshotPacket(window_id, app_pid, self._generation, tuple(changes), tuple(dib.values()),
                                   focus, base_generation=base)

    def _emit(self, pkt: StateSnapshotPacket) -> StateSnapshotPacket | None:
        try:
            self._send(pkt)
        except Exception:  # noqa: BLE001 - the view is an aid: a failed send must not stop the loop
            log.warning("could not send a snapshot; the next one will be full", exc_info=True)
            with self._lock:
                self._need_full = True
            return None
        return pkt


__all__ = ["SnapshotPublisher", "FIELDS", "DIB_FIELDS", "MAX_NODES", "MAX_TEXT"]
