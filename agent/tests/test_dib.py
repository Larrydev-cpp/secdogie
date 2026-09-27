"""Headless tests for the DIB shared-memory framebuffer reader.

The "shared memory" is an ordinary ``bytearray``/``ctypes`` buffer and the
producer's seqlock is a small counter object, so the whole tear-free protocol is
exercised on CI with no OS, no second process and no real shared memory."""
from __future__ import annotations

import ctypes
import hashlib

import pytest
from secdogie_agent.observation import VisualReference
from secdogie_agent.perception.dib import (
    DEFAULT_MAX_SLICE_BYTES,
    DibBoundsError,
    DibBudgetError,
    DibFormatError,
    DIBFrameBuffer,
    DibTearError,
    HeapDibReader,
    bytes_per_pixel,
    read_tearfree,
)


class FakeSeqlock:
    """A producer's seqlock counter. ``bump_on_read`` simulates a writer that
    commits a frame partway through the reader's copy (torn read)."""

    def __init__(self, value=0, *, bump_on_read=0):
        self.value = value
        self._bump_on_read = bump_on_read
        self.reads = 0

    def read_seq(self):
        self.reads += 1
        v = self.value
        if self._bump_on_read and self.reads == 1:
            # After the reader's first sample, a writer runs to completion.
            self.value += self._bump_on_read
        return v


def make_frame(width=2, height=2, fmt="BGRA8888", *, stride=None, pad=0, seq=0, data=None, seqlock=None):
    bpp = bytes_per_pixel(fmt)
    stride = width * bpp + pad if stride is None else stride
    size = stride * height
    if data is None:
        data = bytes(range(1, size + 1)) if size <= 256 else bytes(size)
    buf = bytearray(data)
    lock = seqlock if seqlock is not None else FakeSeqlock(seq)
    fb = DIBFrameBuffer(
        address=0xDEAD,
        width=width,
        height=height,
        stride=stride,
        pixel_format=fmt,
        size_bytes=size,
        seqlock=seq,
        _buffer=buf,
        _seq=lock.read_seq,
    )
    return fb, buf, lock


# --- pixel formats / geometry -------------------------------------------------


@pytest.mark.parametrize(
    "fmt,bpp", [("RGBA8888", 4), ("BGRA8888", 4), ("RGB888", 3), ("RGB565", 2), ("GRAY8", 1)]
)
def test_bytes_per_pixel(fmt, bpp):
    assert bytes_per_pixel(fmt) == bpp
    assert bytes_per_pixel(fmt.lower()) == bpp


def test_unknown_format_rejected():
    with pytest.raises(DibFormatError):
        bytes_per_pixel("YUV420")


def test_validate_rejects_bad_geometry():
    with pytest.raises(DibFormatError):  # stride < row
        DIBFrameBuffer(0, 4, 2, stride=4, pixel_format="RGBA8888").validate()
    with pytest.raises(DibFormatError):  # size < stride*height
        DIBFrameBuffer(0, 2, 2, stride=8, pixel_format="RGBA8888", size_bytes=8).validate()
    with pytest.raises(DibFormatError):  # non-positive dims
        DIBFrameBuffer(0, 0, 2, stride=0, pixel_format="RGBA8888").validate()


def test_frame_bytes_defaults_to_stride_times_height():
    fb = DIBFrameBuffer(0, 2, 3, stride=8, pixel_format="RGBA8888")
    assert fb.frame_bytes == 24
    assert fb.min_stride == 8


# --- seqlock: tear-free read --------------------------------------------------


def test_read_tearfree_clean():
    lock = FakeSeqlock(4)
    data, seq = read_tearfree(lambda: b"frame", lock.read_seq)
    assert data == b"frame"
    assert seq == 4


def test_read_tearfree_odd_lock_retries_until_even():
    seqs = iter([1, 3, 6, 6])  # write in progress twice, then settled
    data, seq = read_tearfree(lambda: b"ok", lambda: next(seqs), retries=3)
    assert (data, seq) == (b"ok", 6)


def test_read_tearfree_sequence_change_is_torn():
    # A writer commits between the pre- and post-read samples on every attempt.
    seqs = iter([2, 4, 6, 8, 10, 12, 14, 16])
    with pytest.raises(DibTearError, match="sequence changed"):
        read_tearfree(lambda: b"x", lambda: next(seqs), retries=3)


def test_read_tearfree_all_odd_raises():
    with pytest.raises(DibTearError, match="write in progress"):
        read_tearfree(lambda: b"x", lambda: 1, retries=2)


def test_read_bytes_error_is_not_retried():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise DibBoundsError("oob")

    with pytest.raises(DibBoundsError):
        read_tearfree(boom, lambda: 0, retries=5)
    assert calls["n"] == 1  # raised on the first attempt, no retry spin


# --- DIBFrameBuffer.get_bytes / read -----------------------------------------


def test_get_bytes_returns_whole_frame_tearfree():
    fb, buf, lock = make_frame(width=2, height=2, pad=0, seq=8)
    data = fb.get_bytes()
    assert data == bytes(buf)
    assert len(data) == 16
    assert lock.reads == 2  # sampled before and after


def test_read_reports_settled_sequence():
    fb, _, _ = make_frame(seq=10)
    data, seq = fb.read()
    assert seq == 10 and len(data) == 16


def test_get_bytes_backs_off_on_concurrent_write():
    # Odd lock (writer mid-frame) on the first sample, settled afterwards.
    lock = FakeSeqlock(5)

    def read_seq():
        lock.reads += 1
        return 5 if lock.reads == 1 else 6

    fb, _, _ = make_frame(seq=6, seqlock=type("L", (), {"read_seq": staticmethod(read_seq)})())
    data = fb.get_bytes()
    assert len(data) == 16


def test_torn_frame_raises_from_get_bytes():
    fb, _, _ = make_frame(seq=2, seqlock=FakeSeqlock(2, bump_on_read=2))
    with pytest.raises(DibTearError):
        fb.get_bytes(retries=0)


def test_out_of_bounds_slice_raises():
    fb, _, _ = make_frame(width=2, height=2)  # needs 16 bytes
    short = DIBFrameBuffer(
        0, 2, 2, stride=8, pixel_format="BGRA8888", size_bytes=16, _buffer=bytearray(8), _seq=lambda: 0
    )
    with pytest.raises(DibBoundsError):
        short.get_bytes()


def test_offset_reads_from_within_a_larger_heap():
    heap = bytearray(range(64))
    fb = DIBFrameBuffer(
        0, 2, 2, stride=8, pixel_format="GRAY8", size_bytes=16, offset=16, _buffer=heap, _seq=lambda: 0
    )
    # GRAY8 2x2 with stride 8 -> 16 bytes; from offset 16.
    assert fb.get_bytes() == bytes(range(16, 32))


def test_budget_cap_rejects_oversized_frame():
    fb, _, _ = make_frame(width=4, height=4)  # 64 bytes
    with pytest.raises(DibBudgetError):
        fb.get_bytes(max_bytes=32)


def test_ctypes_buffer_is_readable():
    raw = (ctypes.c_ubyte * 16)(*range(16))
    fb = DIBFrameBuffer(
        0, 2, 2, stride=8, pixel_format="RGBA8888", size_bytes=16, _buffer=raw, _seq=lambda: 0
    )
    assert fb.get_bytes() == bytes(range(16))


# --- stride conversion --------------------------------------------------------


def test_iter_rows_strips_stride_padding():
    # 2px wide GRAY8 (row = 2 bytes) but stride 4 => 2 padding bytes per row.
    fb, _, _ = make_frame(width=2, height=3, fmt="GRAY8", stride=4)
    data = fb.get_bytes()
    rows = list(fb.iter_rows(data))
    assert [len(r) for r in rows] == [2, 2, 2]
    # Each row is the leading (unpadded) bytes of its stride window.
    assert rows[0] == data[0:2]
    assert rows[1] == data[4:6]
    assert rows[2] == data[8:10]


# --- to_reference / VisualReference -------------------------------------------


def test_to_reference_hashes_settled_bytes():
    fb, buf, _ = make_frame(width=2, height=2, seq=12)
    data, seq = fb.read()
    ref = fb.to_reference(data, seq)
    assert isinstance(ref, VisualReference)
    assert ref.pixels_available is True
    assert ref.content_hash == hashlib.sha256(bytes(buf)).hexdigest()
    assert ref.seqlock == 12
    assert ref.stride == 8
    assert ref.pixel_format == "BGRA8888"
    assert ref.bit_count == 32
    assert ref.size_bytes == 16
    assert ref.width == 2 and ref.height == 2


def test_reference_hash_tracks_pixel_content():
    fb_a, _, _ = make_frame(data=bytes([1] * 16))
    fb_b, _, _ = make_frame(data=bytes([2] * 16))
    ref_a = fb_a.to_reference(*fb_a.read())
    ref_b = fb_b.to_reference(*fb_b.read())
    assert ref_a.content_hash != ref_b.content_hash


# --- HeapDibReader ------------------------------------------------------------


def test_heap_reader_locates_frame_and_defaults_budget():
    fb, _, _ = make_frame()
    reader = HeapDibReader(lambda node: fb if node == "canvas" else None)
    assert reader.max_slice_bytes == DEFAULT_MAX_SLICE_BYTES
    assert reader.inspect("canvas") is fb
    assert reader.inspect("other") is None
