"""The Direct Inspection Buffer: wire format, fail-closed parsing, the read-only
reader, and how DIB readings fuse with AX. Headless: buffers are built with the
reference encoder and read from real temporary files."""
from __future__ import annotations

import contextlib
import os
import struct
import zlib

import pytest
from secdogie_agent.perception import (
    CONFLICT_GENERATION,
    CONFLICT_WINDOW_IDENTITY,
    DibFormatError,
    DibNode,
    DibReader,
    DibTornRead,
    Geometry,
    SemanticNode,
    encode_dib,
    fuse,
    observe_ax,
    observe_dib,
    parse_dib,
)
from secdogie_agent.perception import dib as dibmod

HDR = 64
NODE = 52
# header field offsets
OFF_MAGIC, OFF_VERSION, OFF_HSIZE, OFF_SEQ = 0, 4, 6, 8
OFF_COUNT, OFF_POOL, OFF_CRC, OFF_FLAGS, OFF_RESERVED = 44, 48, 52, 56, 60
# node field offsets (relative to the record)
N_ID, N_PARENT, N_FLAGS, N_PAD, N_W = 0, 4, 8, 10, 20
N_ROLE_OFF, N_ROLE_LEN, N_NAME_OFF, N_NAME_LEN = 28, 32, 36, 40


def _scene():
    return [
        DibNode(1, None, "cad.canvas", "Drawing", bounds=Geometry(0, 0, 800, 600)),
        DibNode(2, 1, "cad.layer", "结构", bounds=Geometry(0, 0, 800, 600)),
        DibNode(3, 2, "cad.polyline", "外墙", value="L=12.5m", bounds=Geometry(10, 10, 100, 5),
                selected=True),
        DibNode(4, 2, "cad.hidden", "guide", visible=False),
    ]


def _buf(nodes=None, *, generation=7, window_id=42, app_pid=1234, seq=0):
    return encode_dib(_scene() if nodes is None else nodes, generation=generation,
                      window_id=window_id, app_pid=app_pid,
                      timestamp_ns=1_700_000_000_000_000_000, seq=seq)


def _patch(buf, offset, fmt, value, *, rehash=True):
    """Overwrite one field; by default recompute crc32 so the targeted check --
    not the checksum -- is what rejects the buffer."""
    out = bytearray(buf)
    struct.pack_into("<" + fmt, out, offset, value)
    if rehash:
        count, pool = struct.unpack_from("<II", out, OFF_COUNT)
        body = bytes(out[HDR:HDR + count * NODE + pool])
        struct.pack_into("<I", out, OFF_CRC, zlib.crc32(body))
    return bytes(out)


def _node(i, field):
    return HDR + i * NODE + field


# --- round trip -------------------------------------------------------------


def test_round_trip_keeps_tree_strings_and_flags():
    snap = parse_dib(_buf())
    assert (snap.window_id, snap.app_pid, snap.generation) == (42, 1234, 7)
    assert [n.node_id for n in snap.nodes] == [1, 2, 3, 4]
    assert [n.name for n in snap.roots()] == ["Drawing"]
    assert [n.node_id for n in snap.children(2)] == [3, 4]
    poly = snap.nodes[2]
    assert poly.name == "外墙" and poly.value == "L=12.5m" and poly.selected and poly.enabled
    assert poly.bounds == Geometry(10, 10, 100, 5)
    assert snap.bounds() == Geometry(0, 0, 800, 600)
    assert snap.timestamp == pytest.approx(1_700_000_000.0)


def test_invisible_nodes_are_not_projected_into_semantics():
    ids = [n.automation_id for n in parse_dib(_buf()).semantic_nodes()]
    assert ids == ["dib:1", "dib:2", "dib:3"]  # dib:4 is not visible


def test_digest_tracks_content_not_just_generation():
    a = parse_dib(_buf())
    changed = _scene()
    changed[2] = DibNode(3, 2, "cad.polyline", "外墙", value="L=13.0m",
                         bounds=Geometry(10, 10, 100, 5), selected=True)
    b = parse_dib(_buf(changed))
    assert a.generation == b.generation and a.digest != b.digest


def test_encoder_refuses_an_odd_seq():
    with pytest.raises(ValueError):
        _buf(seq=1)


# --- fail closed ------------------------------------------------------------


@pytest.mark.parametrize("offset,fmt,value,rehash,needle", [
    (OFF_MAGIC, "4s", b"XDIB", True, "magic"),
    (OFF_VERSION, "H", 2, True, "version"),
    (OFF_HSIZE, "H", 60, True, "header_size"),
    (OFF_FLAGS, "I", 1, True, "flags"),
    (OFF_RESERVED, "I", 1, True, "reserved"),
    (OFF_COUNT, "I", dibmod.MAX_NODES + 1, False, "limit"),
    (OFF_POOL, "I", dibmod.MAX_POOL_BYTES + 1, False, "limit"),
    (_node(1, N_ID), "I", 1, True, "duplicate"),
    (_node(1, N_ID), "I", 0xFFFFFFFF, True, "reserved"),
    (_node(1, N_PARENT), "I", 99, True, "does not exist"),
    (_node(0, N_PARENT), "I", 2, True, "cycle"),
    (_node(0, N_PARENT), "I", 1, True, "cycle"),
    (_node(1, N_FLAGS), "H", 0x10, True, "unknown flag"),
    (_node(1, N_PAD), "H", 1, True, "pad"),
    (_node(1, N_W), "i", -1, True, "negative"),
    (_node(1, N_ROLE_LEN), "I", 4000, True, "outside the string pool"),
    (_node(1, N_ROLE_LEN), "I", dibmod.MAX_STRING_BYTES + 1, True, "limit"),
], ids=["magic", "version", "header_size", "flags", "reserved", "node_count", "pool_size",
        "duplicate_id", "reserved_id", "dangling_parent", "cycle", "self_parent", "flag_bits",
        "pad", "negative_size", "string_out_of_pool", "string_too_long"])
def test_malformed_buffers_are_refused(offset, fmt, value, rehash, needle):
    with pytest.raises(DibFormatError, match=needle):
        parse_dib(_patch(_buf(), offset, fmt, value, rehash=rehash))


def test_crc_mismatch_is_refused():
    buf = bytearray(_buf())
    buf[-1] ^= 0xFF  # flip a string-pool byte, leave the checksum alone
    with pytest.raises(DibFormatError, match="crc32"):
        parse_dib(bytes(buf))


def test_invalid_utf8_is_refused():
    buf = _buf([DibNode(1, None, "canvas", "abc")])
    name_off = struct.unpack_from("<I", buf, _node(0, N_NAME_OFF))[0]
    pool_start = HDR + NODE
    with pytest.raises(DibFormatError, match="UTF-8"):
        parse_dib(_patch(buf, pool_start + name_off, "B", 0xFF))


def test_truncation_is_refused():
    buf = _buf()
    with pytest.raises(DibFormatError, match="truncated header"):
        parse_dib(buf[:10])
    with pytest.raises(DibFormatError, match="truncated body"):
        parse_dib(buf[:-1])


def test_depth_is_bounded():
    chain = [DibNode(i, None if i == 1 else i - 1, "group") for i in range(1, dibmod.MAX_DEPTH + 1)]
    parse_dib(_buf(chain))  # exactly at the limit
    deeper = chain + [DibNode(dibmod.MAX_DEPTH + 1, dibmod.MAX_DEPTH, "group")]
    with pytest.raises(DibFormatError, match="deeper"):
        _buf(deeper)


def test_odd_seq_is_a_torn_read():
    with pytest.raises(DibTornRead):
        parse_dib(_patch(_buf(), OFF_SEQ, "Q", 3))


# --- the read-only reader ---------------------------------------------------


def _write(path, data):
    path.write_bytes(data)
    return path


def test_reader_reads_a_published_buffer(tmp_path):
    buf = _buf()
    snap = DibReader(_write(tmp_path / "app.dib", buf)).snapshot()
    assert snap == parse_dib(buf)


def test_reader_mapping_is_read_only(tmp_path):
    reader = DibReader(_write(tmp_path / "app.dib", _buf()))
    with reader._map() as mapping:
        with pytest.raises(TypeError):
            mapping[0:1] = b"X"


def test_reader_gives_up_on_a_writer_that_never_finishes(tmp_path):
    path = _write(tmp_path / "app.dib", _patch(_buf(), OFF_SEQ, "Q", 5))
    with pytest.raises(DibTornRead):
        DibReader(path).snapshot(retries=2)


def test_reader_retries_when_the_buffer_changes_mid_read(monkeypatch):
    good = _buf(seq=2)
    first = bytearray(good)
    calls = []
    real_parse = dibmod._parse

    def parse_during_a_write(view):
        calls.append(1)
        if len(calls) == 1:
            view[OFF_SEQ:OFF_SEQ + 8] = struct.pack("<Q", 4)  # the writer moved on meanwhile
        return real_parse(view)

    monkeypatch.setattr(dibmod, "_parse", parse_during_a_write)

    class Scripted(DibReader):
        def __init__(self, buffers):
            super().__init__("unused")
            self._buffers = iter(buffers)

        @contextlib.contextmanager
        def _map(self):
            yield next(self._buffers)

    snap = Scripted([first, bytearray(good)]).snapshot()
    assert len(calls) == 2 and snap.seq == 2


def test_reader_refuses_a_directory_and_an_oversized_file(tmp_path):
    with pytest.raises(DibFormatError, match="regular file"):
        DibReader(tmp_path).snapshot()
    path = _write(tmp_path / "big.dib", _buf())
    with pytest.raises(DibFormatError, match="limit"):
        DibReader(path, max_bytes=100).snapshot()


def test_reader_refuses_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        DibReader(tmp_path / "absent.dib").snapshot()


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "symlink"),
                    reason="needs O_NOFOLLOW and symlinks")
def test_reader_does_not_follow_a_symlink(tmp_path):
    real = _write(tmp_path / "real.dib", _buf())
    link = tmp_path / "link.dib"
    os.symlink(real, link)
    with pytest.raises(OSError):
        DibReader(link).snapshot()


# --- fusion with AX ---------------------------------------------------------


def _ax(*, window_id=42, app_pid=1234, generation=7):
    canvas = SemanticNode(role="AXGroup", name="Canvas", automation_id="canvas",
                          bounds=Geometry(0, 0, 800, 600))
    return observe_ax(window_id=window_id, app_pid=app_pid, generation=generation,
                      geometry=Geometry(0, 0, 800, 600), semantic_nodes=(canvas,), timestamp=1.0)


def test_dib_fills_the_canvas_ax_cannot_see():
    ax = _ax()
    dib = observe_dib(parse_dib(_buf()), timestamp=1.0)
    res = fuse([ax, dib])
    assert res.clean
    ids = [n.automation_id for n in res.fused.semantic_nodes]
    assert ids == ["canvas", "dib:1", "dib:2", "dib:3"]  # AX first, then the scene
    assert res.fused.dib is dib.dib


def test_a_buffer_claiming_another_window_is_set_aside():
    ax = _ax()
    impostor = observe_dib(parse_dib(_buf(window_id=99, app_pid=666)), timestamp=1.0)
    res = fuse([ax, impostor])
    assert CONFLICT_WINDOW_IDENTITY in {c.kind for c in res.conflicts}
    assert impostor in res.foreign and impostor not in res.contributors
    assert not any(n.automation_id.startswith("dib:") for n in res.fused.semantic_nodes)
    assert res.fused.dib is None


def test_a_stale_buffer_generation_is_excluded():
    ax = _ax(generation=8)
    stale = observe_dib(parse_dib(_buf(generation=7)), timestamp=1.0)
    res = fuse([ax, stale])
    assert CONFLICT_GENERATION in {c.kind for c in res.conflicts}
    assert stale in res.stale
    assert res.fused.dib is None
