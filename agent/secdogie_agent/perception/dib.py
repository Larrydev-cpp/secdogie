"""DIB (Direct Inspection Buffer): tear-free reads of a shared-memory framebuffer.

Some UI surfaces are custom-drawn or GPU-composited, so the accessibility tree
knows their box but nothing about their content. When a *cooperating* producer
publishes that surface's pixels in a shared memory region, this module reads one
settled frame out of it without tearing.

The synchronisation is a **seqlock**, the standard single-writer / many-reader
lock-free protocol (Linux kernel timekeeping, wl_shm-style buffers, game engine
frame handoff all use it):

  * the producer makes the sequence counter **odd** while it is writing a frame
    and leaves it **even** once the frame is settled, incrementing it once per
    frame;
  * a reader samples the counter, copies the bytes, then samples again. An odd
    first sample means a write is in progress; a changed sample means the copy
    raced a write. Either way the copy may be torn and is discarded (and, up to
    a small retry budget, re-attempted).

This is a *cooperative* protocol: it only works because the writer maintains the
counter, so it is not a way to read memory a process did not deliberately
publish. Everything here operates on an ordinary Python buffer object the caller
supplies (a ``bytearray``, ``memoryview``, ``mmap`` or ``ctypes`` array) and a
callable that returns the current sequence number. There are no OS calls and no
attaching to other processes -- the OS-facing "map the region, find the counter"
step lives behind the ``SeqReader`` / buffer seam and is provided from outside,
which is what keeps this module pure and unit-testable on CI.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..observation import Budget, VisualReference

# Bytes per pixel for the formats a producer may publish. The key is the format
# string carried on the frame; unknown formats are rejected rather than guessed.
PIXEL_FORMAT_BYTES: dict[str, int] = {
    "RGBA8888": 4,
    "BGRA8888": 4,
    "ARGB8888": 4,
    "ABGR8888": 4,
    "RGBX8888": 4,
    "BGRX8888": 4,
    "RGB888": 3,
    "BGR888": 3,
    "RGB565": 2,
    "BGR565": 2,
    "GRAY8": 1,
}

# Hard ceiling on a single frame copy, so a bogus size can't allocate the heap
# out from under us. Defaults to the perception DIB budget.
DEFAULT_MAX_SLICE_BYTES = Budget().max_dib_bytes

# Default number of *extra* attempts when a read is torn (odd lock or a racing
# write). Small: a producer that never settles is a fault, not something to spin on.
DEFAULT_RETRIES = 3


class DibError(RuntimeError):
    """Base class for a failed DIB read; the AX node is always kept regardless."""


class DibTearError(DibError):
    """The frame could not be read tear-free within the retry budget (the writer
    held the lock odd, or the sequence changed under every attempt)."""


class DibBudgetError(DibError):
    """The frame's byte size exceeds the per-read ceiling."""


class DibBoundsError(DibError):
    """The requested slice falls outside the backing buffer (heap out-of-bounds)."""


class DibFormatError(DibError):
    """The frame geometry is inconsistent (unknown format, stride < row, or a
    size that doesn't cover the declared rows)."""


def bytes_per_pixel(pixel_format: str) -> int:
    try:
        return PIXEL_FORMAT_BYTES[pixel_format.upper()]
    except KeyError:
        raise DibFormatError(f"unknown pixel format: {pixel_format!r}") from None


ReadBytesFn = Callable[[], bytes]
SeqFn = Callable[[], int]


@runtime_checkable
class SeqReader(Protocol):
    def read_seq(self) -> int:
        """The producer's current sequence counter (odd == write in progress)."""
        ...


def read_tearfree(
    read_bytes: ReadBytesFn, read_seq: SeqFn, *, retries: int = DEFAULT_RETRIES
) -> tuple[bytes, int]:
    """Copy bytes guarded by a seqlock, retrying past torn reads.

    Returns ``(data, seq)`` where ``seq`` is the settled even counter the copy is
    consistent with. Raises :class:`DibTearError` if every attempt is torn.
    Exceptions from ``read_bytes`` itself (e.g. out-of-bounds) are not retried --
    they are a fault in the read, not a race -- and propagate unchanged."""
    reason = "no attempt made"
    for _ in range(max(0, retries) + 1):
        s1 = read_seq()
        if s1 & 1:
            reason = f"write in progress (seq={s1} odd)"
            continue
        data = read_bytes()  # a bounds/format error here is not a tear: let it raise
        s2 = read_seq()
        if s1 == s2:
            return data, s1
        reason = f"sequence changed during read ({s1} -> {s2})"
    raise DibTearError(reason)


@dataclass(frozen=True)
class DIBFrameBuffer:
    """A handle to one framebuffer in a cooperating producer's shared memory.

    The metadata fields describe the frame; ``_buffer`` is the backing Python
    buffer object and ``_seq`` returns the live sequence counter. Those two are
    excluded from equality/repr (compare=False) so two handles to the same frame
    compare by their metadata, not by buffer identity. Nothing is read until
    :meth:`read` / :meth:`get_bytes` is called."""

    address: int
    width: int
    height: int
    stride: int
    pixel_format: str
    size_bytes: int = 0  # 0 => derive as stride * height
    seqlock: int = 0  # last-known counter (informational; read() re-samples live)
    offset: int = 0  # byte offset of the frame within _buffer
    _buffer: object = field(default=None, compare=False, repr=False)
    _seq: SeqFn | None = field(default=None, compare=False, repr=False)

    # -- geometry --------------------------------------------------------------

    @property
    def bytes_per_pixel(self) -> int:
        return bytes_per_pixel(self.pixel_format)

    @property
    def min_stride(self) -> int:
        return self.width * self.bytes_per_pixel

    @property
    def frame_bytes(self) -> int:
        return self.size_bytes if self.size_bytes > 0 else self.stride * self.height

    def validate(self) -> None:
        """Reject inconsistent geometry before touching memory."""
        if self.width <= 0 or self.height <= 0:
            raise DibFormatError(f"non-positive dimensions {self.width}x{self.height}")
        bpp = self.bytes_per_pixel  # raises DibFormatError on unknown format
        if self.stride < self.width * bpp:
            raise DibFormatError(f"stride {self.stride} < row {self.width * bpp}")
        if self.size_bytes and self.size_bytes < self.stride * self.height:
            raise DibFormatError(
                f"size_bytes {self.size_bytes} < stride*height {self.stride * self.height}"
            )
        if self.offset < 0:
            raise DibBoundsError(f"negative offset {self.offset}")

    # -- reading ---------------------------------------------------------------

    def _slice(self, n: int) -> bytes:
        if self._buffer is None:
            raise DibBoundsError("frame has no backing buffer")
        mv = memoryview(self._buffer).cast("B")
        end = self.offset + n
        if self.offset < 0 or end > len(mv):
            raise DibBoundsError(
                f"slice [{self.offset}:{end}] out of buffer of {len(mv)} bytes"
            )
        return bytes(mv[self.offset : end])

    def read(self, *, max_bytes: int = DEFAULT_MAX_SLICE_BYTES, retries: int = DEFAULT_RETRIES) -> tuple[bytes, int]:
        """Tear-free read of the whole frame. Returns ``(data, settled_seq)``.

        Order of checks: geometry, then the byte ceiling (:class:`DibBudgetError`),
        then the seqlock read (:class:`DibTearError`), with an out-of-bounds slice
        surfacing as :class:`DibBoundsError`."""
        self.validate()
        n = self.frame_bytes
        if n > max_bytes:
            raise DibBudgetError(f"frame of {n} bytes exceeds cap {max_bytes}")
        seq = self._seq if self._seq is not None else (lambda: self.seqlock)
        return read_tearfree(lambda: self._slice(n), seq, retries=retries)

    def get_bytes(self, *, max_bytes: int = DEFAULT_MAX_SLICE_BYTES, retries: int = DEFAULT_RETRIES) -> bytes:
        """The frame's raw bytes, tear-free (see :meth:`read`)."""
        return self.read(max_bytes=max_bytes, retries=retries)[0]

    def iter_rows(self, data: bytes) -> Iterator[bytes]:
        """Split a frame's bytes into ``height`` rows of ``width*bpp``, dropping
        each row's stride padding. The stride-to-tight-rows conversion."""
        row = self.min_stride
        for y in range(self.height):
            start = y * self.stride
            yield data[start : start + row]

    def to_reference(self, data: bytes, seq: int) -> VisualReference:
        """A hashable :class:`VisualReference` for a frame already read: identity
        plus a content hash of the settled bytes, for fusion. The bytes are
        hashed, not retained on the reference."""
        return VisualReference(
            address=self.address,
            width=self.width,
            height=self.height,
            bit_count=self.bytes_per_pixel * 8,
            source="heap",
            content_hash=hashlib.sha256(data).hexdigest(),
            pixels_available=True,
            stride=self.stride,
            pixel_format=self.pixel_format.upper(),
            size_bytes=len(data),
            seqlock=seq,
        )


FrameLocator = Callable[[object], "DIBFrameBuffer | None"]


class HeapDibReader:
    """A ``DibProvider`` that maps a blind AX node to a shared-memory frame.

    ``locator`` turns a ``SemanticNode`` into the ``DIBFrameBuffer`` that backs
    it (by automation id, role, geometry -- whatever the integration knows), or
    None when the node has no published frame. ``max_slice_bytes`` is the hard
    per-frame ceiling passed down to :meth:`DIBFrameBuffer.read`.

    ``inspect`` returns the frame *handle*; the adapter performs the tear-free
    read so it can account for tears vs. budget vs. bounds in its report. A
    locator that raises has that surfaced by the adapter like any other DIB
    failure -- the AX node is never lost."""

    def __init__(self, locator: FrameLocator, *, max_slice_bytes: int = DEFAULT_MAX_SLICE_BYTES) -> None:
        self.locator = locator
        self.max_slice_bytes = max_slice_bytes

    def inspect(self, node) -> DIBFrameBuffer | None:
        return self.locator(node)


__all__ = [
    "PIXEL_FORMAT_BYTES",
    "DEFAULT_MAX_SLICE_BYTES",
    "DEFAULT_RETRIES",
    "DibError",
    "DibTearError",
    "DibBudgetError",
    "DibBoundsError",
    "DibFormatError",
    "DIBFrameBuffer",
    "HeapDibReader",
    "SeqReader",
    "bytes_per_pixel",
    "read_tearfree",
]
