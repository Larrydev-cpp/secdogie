"""DIB -- the Direct Inspection Buffer (Slice D0).

Some of what an agent must perceive is invisible to the accessibility tree:
self-drawn controls, a Canvas or WebGL scene, a CAD drawing surface. The usual
answer is to screenshot it and ask a vision model. This project does not
screenshot. Instead, the application -- or a plugin the operator installs into
it -- *publishes* a small structured description of what it is showing: a
Direct Inspection Buffer. The agent maps that buffer read-only and parses it into
the same typed ``Observation`` the AX track produces.

What makes it safe to read automatically:

  * **Cooperative, not intrusive.** The buffer is a file or shared-memory region
    the application itself writes and exposes. The reader opens it ``O_RDONLY``
    and maps it ``ACCESS_READ``. It never opens a process handle and never reads
    another process's memory: no ReadProcessMemory, no ptrace, no
    ``/proc/<pid>/mem``. An application that exposes no buffer yields no DIB
    observation -- the agent does not go looking inside it.
  * **Read-only and zero-copy.** Parsing runs on a ``memoryview`` over the
    mapping; only short strings are decoded out of it. Nothing is written, and a
    write to the mapping raises ``TypeError``.
  * **Bounded and fail-closed.** Every size is capped and every field checked;
    a malformed buffer raises ``DibFormatError`` instead of being partially
    trusted. There is no pixel data anywhere in the format.
  * **Consistent under a live writer.** A seqlock: the writer makes ``seq`` odd
    before it edits the buffer and even again after. The reader re-checks
    ``seq`` around each parse and retries a torn read.

Wire format v1 (little-endian, fixed-size records)::

    header (64 bytes)  magic "SDIB", version, header_size, seq, generation,
                       timestamp_ns, window_id, app_pid, node_count, pool_size,
                       crc32 (node table + string pool), flags (0), reserved (0)
    node table         node_count x 52-byte records: node_id, parent_id
                       (0xFFFFFFFF = root), flags, pad, x, y, w, h, and
                       (offset, length) of role / name / value in the pool
    string pool        UTF-8 bytes

``window_id``, ``app_pid`` and ``generation`` are the writer's own claims. Fusion
checks them against the AX track: a buffer that claims a different window is set
aside as a conflict, never silently merged.
"""
from __future__ import annotations

import contextlib
import hashlib
import mmap
import os
import stat
import struct
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from .observation import Geometry, SemanticNode

MAGIC = b"SDIB"
VERSION = 1
HEADER = struct.Struct("<4sHHQQQQIIIIII")  # 64 bytes
NODE = struct.Struct("<IIHHiiiiIIIIII")  # 52 bytes
NO_PARENT = 0xFFFFFFFF

FLAG_VISIBLE = 0x1
FLAG_ENABLED = 0x2
FLAG_FOCUSED = 0x4
FLAG_SELECTED = 0x8
_KNOWN_FLAGS = FLAG_VISIBLE | FLAG_ENABLED | FLAG_FOCUSED | FLAG_SELECTED

MAX_BUFFER_BYTES = 8 * 1024 * 1024
MAX_NODES = 20_000
MAX_POOL_BYTES = 4 * 1024 * 1024
MAX_STRING_BYTES = 4096
MAX_DEPTH = 256

_SEQ = struct.Struct("<Q")
_SEQ_OFFSET = 8  # after magic (4) + version (2) + header_size (2)


class DibError(ValueError):
    """Base class for a DIB that cannot be turned into an observation."""


class DibFormatError(DibError):
    """The buffer is malformed, oversized or inconsistent. Never partially used."""


class DibTornRead(DibError):
    """The writer was mid-update on every attempt (odd or changing ``seq``)."""


@dataclass(frozen=True)
class DibNode:
    """One element of the application's published scene: a control, a canvas
    item, a CAD entity. ``bounds`` are screen pixels, as AX bounds are."""

    node_id: int
    parent_id: int | None
    role: str
    name: str = ""
    value: str = ""
    bounds: Geometry = field(default_factory=Geometry)
    visible: bool = True
    enabled: bool = True
    focused: bool = False
    selected: bool = False

    @property
    def flags(self) -> int:
        return ((FLAG_VISIBLE if self.visible else 0) | (FLAG_ENABLED if self.enabled else 0)
                | (FLAG_FOCUSED if self.focused else 0) | (FLAG_SELECTED if self.selected else 0))


@dataclass(frozen=True)
class DibSnapshot:
    """One consistent reading of a buffer: its header claims and its node tree."""

    seq: int
    generation: int
    timestamp_ns: int
    window_id: int
    app_pid: int
    crc32: int
    body_sha256: str
    nodes: tuple[DibNode, ...]
    byte_size: int

    @property
    def timestamp(self) -> float:
        return self.timestamp_ns / 1e9

    @property
    def digest(self) -> str:
        """Content identity: the claimed window and generation plus a SHA-256 of
        the node table and string pool (crc32 only guards against corruption)."""
        material = f"dib:v{VERSION}:{self.window_id}:{self.app_pid}:{self.generation}:{self.body_sha256}"
        return hashlib.sha256(material.encode()).hexdigest()

    def roots(self) -> tuple[DibNode, ...]:
        return tuple(n for n in self.nodes if n.parent_id is None)

    def children(self, node_id: int) -> tuple[DibNode, ...]:
        return tuple(n for n in self.nodes if n.parent_id == node_id)

    def bounds(self) -> Geometry:
        """The union of the root nodes' bounds (empty when none has any)."""
        boxes = [n.bounds for n in self.roots() if n.bounds.valid]
        if not boxes:
            return Geometry()
        x0 = min(b.x for b in boxes)
        y0 = min(b.y for b in boxes)
        x1 = max(b.x + b.w for b in boxes)
        y1 = max(b.y + b.h for b in boxes)
        return Geometry(x0, y0, x1 - x0, y1 - y0)

    def semantic_nodes(self) -> tuple[SemanticNode, ...]:
        """Visible nodes, projected onto the same ``SemanticNode`` the AX track
        produces, so targeting and the action gate treat both tracks alike."""
        return tuple(
            SemanticNode(role=n.role, name=n.name, automation_id=f"dib:{n.node_id}",
                         bounds=n.bounds, enabled=n.enabled)
            for n in self.nodes if n.visible
        )


def _string(pool: memoryview, offset: int, length: int, what: str) -> str:
    if length > MAX_STRING_BYTES:
        raise DibFormatError(f"{what} is {length} bytes; the limit is {MAX_STRING_BYTES}")
    if offset > len(pool) or length > len(pool) - offset:
        raise DibFormatError(f"{what} points outside the string pool")
    try:
        return str(pool[offset:offset + length], "utf-8")
    except UnicodeDecodeError as exc:
        raise DibFormatError(f"{what} is not valid UTF-8") from exc


def _check_tree(parent_of: dict[int, int | None]) -> None:
    for nid, parent in parent_of.items():
        if parent is not None and parent not in parent_of:
            raise DibFormatError(f"node {nid} names parent {parent}, which does not exist")
    depth: dict[int, int] = {}
    for start in parent_of:
        chain: list[int] = []
        on_chain: set[int] = set()
        cur: int | None = start
        while cur is not None and cur not in depth:
            if cur in on_chain:
                raise DibFormatError(f"node {cur} is its own ancestor (cycle)")
            chain.append(cur)
            on_chain.add(cur)
            cur = parent_of[cur]
        level = depth[cur] if cur is not None else 0
        for nid in reversed(chain):
            level += 1
            if level > MAX_DEPTH:
                raise DibFormatError(f"tree deeper than {MAX_DEPTH}")
            depth[nid] = level


def _parse(view: memoryview) -> DibSnapshot:
    size = len(view)
    if size < HEADER.size:
        raise DibFormatError("truncated header")
    if size > MAX_BUFFER_BYTES:
        raise DibFormatError(f"buffer is {size} bytes; the limit is {MAX_BUFFER_BYTES}")
    (magic, version, header_size, seq, generation, timestamp_ns, window_id, app_pid,
     node_count, pool_size, crc, flags, reserved) = HEADER.unpack_from(view, 0)
    if magic != MAGIC:
        raise DibFormatError("bad magic: not a Direct Inspection Buffer")
    if version != VERSION:
        raise DibFormatError(f"unsupported version {version}")
    if header_size != HEADER.size:
        raise DibFormatError(f"header_size {header_size} != {HEADER.size}")
    if flags or reserved:
        raise DibFormatError("header flags / reserved must be 0 in v1")
    if seq & 1:
        raise DibTornRead("writer is mid-update (odd seq)")
    if node_count > MAX_NODES:
        raise DibFormatError(f"{node_count} nodes; the limit is {MAX_NODES}")
    if pool_size > MAX_POOL_BYTES:
        raise DibFormatError(f"string pool is {pool_size} bytes; the limit is {MAX_POOL_BYTES}")
    table_end = HEADER.size + node_count * NODE.size
    end = table_end + pool_size
    if end > size:
        raise DibFormatError("truncated body")

    with view[HEADER.size:end] as body:
        if zlib.crc32(body) != crc:
            raise DibFormatError("crc32 mismatch")
        body_sha256 = hashlib.sha256(body).hexdigest()

    nodes: list[DibNode] = []
    parent_of: dict[int, int | None] = {}
    with view[table_end:end] as pool:
        for i in range(node_count):
            (nid, parent, nflags, pad, x, y, w, h,
             role_off, role_len, name_off, name_len, value_off, value_len) = NODE.unpack_from(
                view, HEADER.size + i * NODE.size)
            if nid == NO_PARENT:
                raise DibFormatError(f"node id {NO_PARENT:#x} is reserved")
            if nid in parent_of:
                raise DibFormatError(f"duplicate node id {nid}")
            if pad:
                raise DibFormatError(f"node {nid}: pad must be 0")
            if nflags & ~_KNOWN_FLAGS:
                raise DibFormatError(f"node {nid}: unknown flag bits {nflags & ~_KNOWN_FLAGS:#x}")
            if w < 0 or h < 0:
                raise DibFormatError(f"node {nid}: negative size")
            parent_id = None if parent == NO_PARENT else parent
            parent_of[nid] = parent_id
            nodes.append(DibNode(
                node_id=nid,
                parent_id=parent_id,
                role=_string(pool, role_off, role_len, f"node {nid} role"),
                name=_string(pool, name_off, name_len, f"node {nid} name"),
                value=_string(pool, value_off, value_len, f"node {nid} value"),
                bounds=Geometry(x, y, w, h),
                visible=bool(nflags & FLAG_VISIBLE),
                enabled=bool(nflags & FLAG_ENABLED),
                focused=bool(nflags & FLAG_FOCUSED),
                selected=bool(nflags & FLAG_SELECTED),
            ))
    _check_tree(parent_of)
    return DibSnapshot(seq=seq, generation=generation, timestamp_ns=timestamp_ns,
                       window_id=window_id, app_pid=app_pid, crc32=crc, body_sha256=body_sha256,
                       nodes=tuple(nodes), byte_size=end)


def parse_dib(buf) -> DibSnapshot:
    """Parse one complete buffer (bytes, bytearray, mmap or memoryview). Pure: no
    I/O, nothing retained that references ``buf``. Raises ``DibFormatError`` for
    anything malformed and ``DibTornRead`` when ``seq`` is odd."""
    with memoryview(buf) as raw, raw.cast("B") as view:
        return _parse(view)


def encode_dib(nodes: Iterable[DibNode], *, generation: int, window_id: int, app_pid: int,
               timestamp_ns: int, seq: int = 0) -> bytes:
    """The reference encoder: what an application or plugin publishes. Used by
    the tests and as the executable spec for writers. A complete buffer has an
    even ``seq``; the result always parses."""
    if seq & 1:
        raise ValueError("a complete buffer has an even seq")
    table = bytearray()
    pool = bytearray()

    def put(text: str) -> tuple[int, int]:
        data = text.encode("utf-8")
        offset = len(pool)
        pool.extend(data)
        return offset, len(data)

    count = 0
    for n in nodes:
        role, name, value = put(n.role), put(n.name), put(n.value)
        parent = NO_PARENT if n.parent_id is None else n.parent_id
        b = n.bounds
        table += NODE.pack(n.node_id, parent, n.flags, 0, b.x, b.y, b.w, b.h, *role, *name, *value)
        count += 1
    body = bytes(table) + bytes(pool)
    header = HEADER.pack(MAGIC, VERSION, HEADER.size, seq, generation, timestamp_ns, window_id,
                         app_pid, count, len(pool), zlib.crc32(body), 0, 0)
    buf = header + body
    parse_dib(buf)  # never emit a buffer the reader would reject
    return buf


class DibReader:
    """Reads a published buffer from ``path``: opened read-only, mapped
    read-only, parsed zero-copy, with seqlock retries against a live writer.

    Filesystem refusals (missing file, a symlink, no permission) surface as
    ``OSError``; a buffer that is present but unusable raises ``DibError``."""

    def __init__(self, path, *, max_bytes: int = MAX_BUFFER_BYTES):
        self.path = os.fspath(path)
        self._max_bytes = min(int(max_bytes), MAX_BUFFER_BYTES)

    @contextlib.contextmanager
    def _map(self) -> Iterator[mmap.mmap]:
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_BINARY", 0))
        fd = os.open(self.path, flags)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise DibFormatError(f"{self.path} is not a regular file")
            if st.st_size < HEADER.size:
                raise DibFormatError("truncated header")
            if st.st_size > self._max_bytes:
                raise DibFormatError(f"{self.path} is {st.st_size} bytes; the limit is {self._max_bytes}")
            mapping = mmap.mmap(fd, st.st_size, access=mmap.ACCESS_READ)
        finally:
            os.close(fd)  # the mapping holds its own reference
        try:
            yield mapping
        finally:
            mapping.close()

    def snapshot(self, *, retries: int = 3) -> DibSnapshot:
        """One consistent reading. Retries up to ``retries`` times while the
        writer is mid-update, then raises ``DibTornRead``."""
        last: DibError = DibTornRead("no attempt made")
        for _ in range(max(0, retries) + 1):
            with self._map() as mapping, memoryview(mapping) as view:
                before = _SEQ.unpack_from(view, _SEQ_OFFSET)[0]
                if before & 1:
                    last = DibTornRead("writer is mid-update (odd seq)")
                    continue
                try:
                    snap = _parse(view)
                except DibTornRead as exc:
                    last = exc
                    continue
                except DibFormatError:
                    if _SEQ.unpack_from(view, _SEQ_OFFSET)[0] != before:
                        last = DibTornRead("buffer changed during the read")
                        continue
                    raise
                if _SEQ.unpack_from(view, _SEQ_OFFSET)[0] != before:
                    last = DibTornRead("buffer changed during the read")
                    continue
                return snap
        raise last


__all__ = [
    "MAGIC",
    "VERSION",
    "NO_PARENT",
    "FLAG_VISIBLE",
    "FLAG_ENABLED",
    "FLAG_FOCUSED",
    "FLAG_SELECTED",
    "MAX_BUFFER_BYTES",
    "MAX_NODES",
    "MAX_POOL_BYTES",
    "MAX_STRING_BYTES",
    "MAX_DEPTH",
    "DibError",
    "DibFormatError",
    "DibTornRead",
    "DibNode",
    "DibSnapshot",
    "parse_dib",
    "encode_dib",
    "DibReader",
]
