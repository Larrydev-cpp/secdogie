"""Content-addressed evidence, distributed over the node mesh.

Learned evidence -- a page's text and accessibility tree, and larger things
later -- is too big for the journal (state.py's rule: large content is NEVER in a
delta payload; a `knowledge` entity references it by content hash). This module is
that content store, and it is built to *hold* any size rather than cap it:

  * Content is split into fixed CHUNK-sized leaf blocks, each named by its own
    sha256. A block fits inside one datagram (even an encrypted one), so blocks
    move over the same transport the journal does.
  * Blocks are gathered under manifest blocks (a manifest lists up to FANOUT
    child hashes); manifests of manifests stack as high as needed, so a Merkle
    DAG of any depth represents content of any size. The root manifest's hash is
    the evidence id. Identical content -> identical hashes, so a block is stored
    once no matter how many pages cite it.

Where the bytes live is a separate concern (`BlockBackend`): the default is a
local directory that each node keeps, and the node mesh replicates blocks between
those directories by hash (see node's "ev" protocol) -- the operator's own
machines are the distributed store, needing no third-party account. A future
object-store backend (S3 / R2 / a Cloudflare Worker) can implement the same
`BlockBackend` without touching the addressing logic.

Integrity is checked on the reader's side, always: every block read back is
re-hashed and rejected if it does not match its name (`open` raises), and a
block is only stored if its bytes hash to the claimed name (`put` refuses).
So whoever serves a block -- a peer, an edge cache -- cannot forge or alter
evidence; trust is in the hash, not the source.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from .state import STATE_EVENT_KIND, StateStore, record_state

CHUNK = 24 * 1024          # leaf block size: fits one (even encrypted) datagram
FANOUT = 512               # child hashes per manifest block
_MANIFEST_TYPE = "secdogie/evidence/manifest/v1"
_PREVIEW_MAX = 280
KNOWLEDGE_TYPE = "knowledge"


def block_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EvidenceError(Exception):
    pass


class EvidenceMissing(EvidenceError):
    """A block referenced by the DAG is not in the backend yet."""

    def __init__(self, digest: str):
        super().__init__(f"missing block {digest}")
        self.digest = digest


class EvidenceCorrupt(EvidenceError):
    """A block's bytes do not hash to the name it was fetched under."""

    def __init__(self, digest: str):
        super().__init__(f"block {digest} failed its hash check")
        self.digest = digest


# ---------------------------------------------------------------------------
# Backends: where the bytes live (addressing above is backend-agnostic)
# ---------------------------------------------------------------------------


class BlockBackend(Protocol):
    def has(self, digest: str) -> bool: ...
    def get(self, digest: str) -> bytes | None: ...
    def put(self, digest: str, data: bytes) -> bool:
        """Store `data` under `digest`. Returns whether it was accepted; an
        implementation MUST reject data that does not hash to `digest`."""
        ...


class LocalBackend:
    """A node's own on-disk block cache: one file per block, named by its hash.
    Together with the node mesh's block exchange, these directories are the
    distributed store."""

    def __init__(self, directory: str | os.PathLike):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        return self.dir / digest

    def has(self, digest: str) -> bool:
        return _is_hash(digest) and self._path(digest).is_file()

    def get(self, digest: str) -> bytes | None:
        try:
            return self._path(digest).read_bytes()
        except (OSError, ValueError):
            return None

    def put(self, digest: str, data: bytes) -> bool:
        if not _is_hash(digest) or block_hash(data) != digest:
            return False
        dest = self._path(digest)
        if dest.exists():
            return True  # content-addressed: immutable, already stored
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)  # atomic within the dir
        except OSError:
            _silent_unlink(tmp)
            return False
        return True


class MemoryBackend:
    """An in-memory block store (tests, and a scratch cache)."""

    def __init__(self):
        self._blocks: dict[str, bytes] = {}

    def has(self, digest: str) -> bool:
        return digest in self._blocks

    def get(self, digest: str) -> bytes | None:
        return self._blocks.get(digest)

    def put(self, digest: str, data: bytes) -> bool:
        if not _is_hash(digest) or block_hash(data) != digest:
            return False
        self._blocks.setdefault(digest, data)
        return True


# ---------------------------------------------------------------------------
# The Merkle DAG over a backend
# ---------------------------------------------------------------------------


def _is_hash(digest) -> bool:
    return isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def _manifest_bytes(children: list[tuple[str, bool]]) -> bytes:
    """Canonical manifest block: each child is (hash, is_manifest)."""
    body = {"t": _MANIFEST_TYPE, "links": [{"h": h, "m": bool(m)} for h, m in children]}
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _parse_manifest(data: bytes) -> list[tuple[str, bool]]:
    obj = json.loads(data)
    if not isinstance(obj, dict) or obj.get("t") != _MANIFEST_TYPE:
        raise EvidenceError("not an evidence manifest")
    out: list[tuple[str, bool]] = []
    for link in obj.get("links") or []:
        h, m = link.get("h"), bool(link.get("m"))
        if not _is_hash(h):
            raise EvidenceError("manifest has a malformed child hash")
        out.append((h, m))
    return out


class EvidenceStore:
    """Reads and writes content as a Merkle DAG over a `BlockBackend`. The root
    is always a manifest, so `open`/`missing` start the same way for any size."""

    def __init__(self, backend: BlockBackend):
        self.backend = backend

    # -- writing -----------------------------------------------------------

    def put_stream(self, reader: BinaryIO) -> str:
        """Chunk `reader` into leaf blocks and build the manifest tree. Streams:
        only one chunk is held at a time. Returns the root hash."""
        level: list[tuple[str, bool]] = []
        while True:
            chunk = reader.read(CHUNK)
            if not chunk:
                break
            level.append((self._put_block(chunk), False))
        return self._build_root(level)

    def put_bytes(self, data: bytes) -> str:
        return self.put_stream(io.BytesIO(data))

    def put_json(self, obj) -> str:
        return self.put_bytes(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"))

    def _put_block(self, data: bytes) -> str:
        digest = block_hash(data)
        if not self.backend.put(digest, data):
            raise EvidenceError(f"backend refused block {digest}")
        return digest

    def _build_root(self, level: list[tuple[str, bool]]) -> str:
        """Fold leaf/manifest nodes up into a single root manifest."""
        while True:
            manifests: list[tuple[str, bool]] = []
            # `or [[]]`: empty content still yields one (empty) root manifest.
            groups = [level[i:i + FANOUT] for i in range(0, len(level), FANOUT)] or [[]]
            for group in groups:
                manifests.append((self._put_block(_manifest_bytes(group)), True))
            if len(manifests) == 1:
                return manifests[0][0]
            level = manifests

    # -- reading -----------------------------------------------------------

    def open(self, root: str) -> Iterator[bytes]:
        """Yield the content's leaf bytes in order, re-hashing every block.
        Raises EvidenceMissing / EvidenceCorrupt rather than returning bad data."""
        yield from self._walk(root, True)

    def read_bytes(self, root: str) -> bytes:
        return b"".join(self.open(root))

    def _walk(self, digest: str, is_manifest: bool) -> Iterator[bytes]:
        data = self.backend.get(digest)
        if data is None:
            raise EvidenceMissing(digest)
        if block_hash(data) != digest:
            raise EvidenceCorrupt(digest)
        if not is_manifest:
            yield data
            return
        for child, child_is_manifest in _parse_manifest(data):
            yield from self._walk(child, child_is_manifest)

    def has_all(self, root: str) -> bool:
        return not self.missing(root)

    def missing(self, root: str) -> list[str]:
        """Hashes the backend still lacks, reachable from `root`. A missing
        manifest hides its subtree, so this converges over rounds: fetch what it
        reports, call again to discover deeper."""
        out: list[str] = []
        seen: set[str] = set()

        def walk(digest: str, is_manifest: bool) -> None:
            if digest in seen:
                return
            seen.add(digest)
            if not self.backend.has(digest):
                out.append(digest)
                return  # cannot recurse into a block we do not have
            if not is_manifest:
                return
            data = self.backend.get(digest)
            if data is None or block_hash(data) != digest:
                out.append(digest)  # corrupt/racy: needs refetch
                return
            for child, child_is_manifest in _parse_manifest(data):
                walk(child, child_is_manifest)

        walk(root, True)
        return out


# ---------------------------------------------------------------------------
# Knowledge entries in the journal (small; reference evidence by root hash)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Knowledge:
    root: str
    url: str
    title: str
    size: int
    preview: str
    ts: float
    author: str = ""


def record_knowledge(journal, *, root: str, url: str, title: str, size: int,
                     preview: str, ts: float | None = None) -> dict:
    """Append a signed `knowledge` entity: only metadata + the evidence root
    hash, never the content. Identified by the root hash, so the same content
    recorded twice is one entity."""
    import time as _time

    if not _is_hash(root):
        raise ValueError("root must be a block hash")
    payload = {
        "root": root,
        "url": url,
        "title": title,
        "size": int(size),
        "preview": (preview or "")[:_PREVIEW_MAX],
        "ts": float(ts if ts is not None else _time.time()),
    }
    return record_state(journal, KNOWLEDGE_TYPE, root, "set", payload)


def knowledge_entries(events_or_journal) -> list[Knowledge]:
    """Every knowledge entity materialized from the journal (converges across the
    mesh like all state)."""
    events = events_or_journal.events() if hasattr(events_or_journal, "events") else events_or_journal
    store = StateStore()
    store.merge_events([e for e in events if e.get("kind") == STATE_EVENT_KIND])
    out: list[Knowledge] = []
    for root, body in store.entities(KNOWLEDGE_TYPE).items():
        out.append(Knowledge(
            root=root, url=body.get("url", ""), title=body.get("title", ""),
            size=int(body.get("size", 0)), preview=body.get("preview", ""),
            ts=float(body.get("ts", 0.0)),
        ))
    return sorted(out, key=lambda k: (k.ts, k.root))


def _silent_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# A backend can be built from a directory path or handed in ready-made.
def backend_from(spec: str | os.PathLike | BlockBackend) -> BlockBackend:
    if hasattr(spec, "get") and hasattr(spec, "put") and hasattr(spec, "has"):
        return spec  # already a backend
    return LocalBackend(spec)


_ = Callable  # (re-exported type hint convenience; keeps import used)

__all__ = [
    "CHUNK", "FANOUT", "KNOWLEDGE_TYPE",
    "BlockBackend", "LocalBackend", "MemoryBackend",
    "EvidenceStore", "EvidenceError", "EvidenceMissing", "EvidenceCorrupt",
    "Knowledge", "record_knowledge", "knowledge_entries",
    "block_hash", "backend_from",
]
