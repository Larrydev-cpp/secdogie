"""Content-addressed evidence: the Merkle DAG holds any size, dedups, streams,
locates what's missing, and rejects tampered blocks -- integrity checked on read,
never trusting the store."""
from __future__ import annotations

import io
import os

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.evidence import (  # noqa: E402
    CHUNK,
    EvidenceCorrupt,
    EvidenceMissing,
    EvidenceStore,
    LocalBackend,
    MemoryBackend,
    block_hash,
    knowledge_entries,
    record_knowledge,
)
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_identity import Identity  # noqa: E402


def _counter():
    n = {"t": 0.0}

    def clock():
        n["t"] += 1.0
        return n["t"]

    return clock


@pytest.mark.parametrize("size", [0, 10, CHUNK, CHUNK + 1, 3 * CHUNK, 20 * 1024 * 1024 + 7])
def test_round_trip_any_size(size):
    data = os.urandom(size)
    store = EvidenceStore(MemoryBackend())
    root = store.put_bytes(data)
    assert store.read_bytes(root) == data
    assert store.has_all(root)


def test_multi_level_manifest_for_large_content():
    # >FANOUT chunks forces manifests-of-manifests
    store = EvidenceStore(MemoryBackend())
    root = store.put_bytes(os.urandom(600 * CHUNK))
    # missing on an empty store returns just the root (can't see deeper yet)
    empty = EvidenceStore(MemoryBackend())
    assert empty.missing(root) == [root]


def test_streaming_write_matches_bytes():
    data = os.urandom(5 * CHUNK + 3)
    a = EvidenceStore(MemoryBackend())
    b = EvidenceStore(MemoryBackend())
    assert a.put_stream(io.BytesIO(data)) == b.put_bytes(data)  # same content -> same root


def test_identical_content_is_stored_once():
    backend = MemoryBackend()
    store = EvidenceStore(backend)
    store.put_bytes(b"the same evidence" * 1000)
    n = len(backend._blocks)
    store.put_bytes(b"the same evidence" * 1000)  # again
    assert len(backend._blocks) == n  # dedup: nothing new


def test_missing_then_repair(tmp_path):
    data = os.urandom(4 * CHUNK)
    src = EvidenceStore(MemoryBackend())
    root = src.put_bytes(data)
    dst_backend = LocalBackend(tmp_path / "blocks")
    dst = EvidenceStore(dst_backend)

    # copy everything but one leaf
    victim = next(h for h, b in src.backend._blocks.items() if len(b) == CHUNK)
    for h, b in src.backend._blocks.items():
        if h != victim:
            dst_backend.put(h, b)
    miss = dst.missing(root)
    assert victim in miss and not dst.has_all(root)
    with pytest.raises(EvidenceMissing):
        dst.read_bytes(root)

    dst_backend.put(victim, src.backend._blocks[victim])
    assert dst.has_all(root) and dst.read_bytes(root) == data


def test_tampering_is_rejected():
    backend = MemoryBackend()
    store = EvidenceStore(backend)
    root = store.put_bytes(b"trustworthy evidence")
    # put refuses bytes that don't match the name
    assert backend.put(block_hash(b"x"), b"not x") is False
    assert backend.put("f" * 64, b"anything") is False
    # a leaf swapped under its hash is caught on read
    leaf = next(h for h, b in backend._blocks.items() if b == b"trustworthy evidence")
    backend._blocks[leaf] = b"forged evidence!!!!!"  # bypass put to simulate a bad store
    with pytest.raises(EvidenceCorrupt):
        store.read_bytes(root)


def test_local_backend_edited_file_is_caught_on_read(tmp_path):
    backend = LocalBackend(tmp_path)
    store = EvidenceStore(backend)
    root = store.put_bytes(b"hello")
    assert store.read_bytes(root) == b"hello"
    # someone edits a stored block file on disk; the read re-hashes and rejects it
    leaf = block_hash(b"hello")
    (backend.dir / leaf).write_bytes(b"HELLO")
    with pytest.raises(EvidenceCorrupt):
        store.read_bytes(root)
    # addressing is stable across backends: same content -> same root
    assert EvidenceStore(MemoryBackend()).put_bytes(b"hello") == root


def test_record_knowledge_keeps_content_out_of_the_journal():
    node = Identity.generate()
    j = Journal(identity=node, clock=_counter())
    store = EvidenceStore(MemoryBackend())
    big = os.urandom(3 * CHUNK)
    root = store.put_bytes(big)
    record_knowledge(j, root=root, url="https://example.com/a", title="A",
                     size=len(big), preview="the first words of the page")

    events = j.events()
    assert len(events) == 1
    body = events[0]["body"]["payload"]
    assert body["root"] == root and body["size"] == len(big)
    # the raw content is nowhere in the journal
    blob = repr(events).encode()
    assert big[:64] not in blob
    assert len(repr(body)) < 1000  # the entry is small

    (entry,) = knowledge_entries(j)
    assert entry.root == root and entry.url == "https://example.com/a" and entry.title == "A"


def test_preview_is_bounded():
    node = Identity.generate()
    j = Journal(identity=node, clock=_counter())
    record_knowledge(j, root="a" * 64, url="u", title="t", size=1, preview="x" * 5000)
    (entry,) = knowledge_entries(j)
    assert len(entry.preview) <= 280
