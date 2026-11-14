"""Carriers: the byte pipes under the DID-signed frame layer (N0).

`DirectUDPTransport` (udp.py) owns everything that makes a frame trustworthy:
the DID signature, optional sealing, the per-sender replay window and the
roaming rules. What it does NOT need to own is the pipe a frame travels on. This
module is that seam:

  * `Carrier` -- one kind of pipe (UDP today; QUIC, WebRTC DataChannel and
    TLS/TCP later). A carrier moves opaque bytes to and from `(host, port)`
    addresses. It authenticates nothing, and nothing above it trusts it: a frame
    is believed because of its signature, never because of the carrier it came
    on.
  * `CarrierSet` -- several carriers under one transport. Every inbound frame
    is tagged with the carrier it arrived on, so a reply can go back the same
    way, and all inbound frames are handed up on ONE delivery thread, in
    arrival order. Upper layers therefore keep the single-threaded inbound path
    they have always had with one UDP socket, whichever thread or event loop a
    carrier receives on.
  * `MemoryNetwork` / `MemoryCarrier` -- an in-process network for headless
    tests and simulations, with per-address blocking to model a blocked path.
  * `AsyncCarrierHost` -- one asyncio event loop on a daemon thread, for the
    asyncio-based carriers to come (aiortc, aioquic). The rest of secdogie is
    threaded; carriers running on this loop hand frames to `CarrierSet`, whose
    delivery thread is never the loop thread, so a slow upper layer can never
    stall the loop.

Addresses: a bare carrier reports inbound frames as `(host, port)`. A
`CarrierSet` reports `(host, port, kind)`; code that only reads `addr[0]` and
`addr[1]` keeps working unchanged. `send(host, port, data, carrier=None)` uses
the primary carrier when no kind is named.

No new crypto, no obfuscation: a carrier is a pipe, and its own security (DTLS,
TLS) is defence in depth on top of the signed frames, never a replacement.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any

log = logging.getLogger("secdogie.transport.carrier")

DatagramFn = Callable[[bytes, tuple], None]  # (frame bytes, source address) -> None

_STOP = object()  # sentinel for the delivery queue


class Carrier(ABC):
    """One pipe for frames. Subclasses set `kind`, `reliable` and `max_frame`,
    and expose the local bound `address`."""

    kind: str = ""
    reliable: bool = False      # True when the pipe itself retransmits and orders
    max_frame: int = 65507      # largest frame the pipe accepts in one send
    address: tuple

    @abstractmethod
    def start(self, on_datagram: DatagramFn) -> None:
        """Begin receiving; call `on_datagram(data, (host, port))` per frame."""

    @abstractmethod
    def send(self, host: str, port: int, data: bytes, carrier: str | None = None) -> None:
        """Send one frame. `carrier` names a kind; a single carrier accepts only
        `None` or its own kind."""

    @abstractmethod
    def close(self) -> None:
        """Stop receiving and release the pipe. Idempotent."""

    @property
    def kinds(self) -> frozenset[str]:
        """The carrier kinds `send` accepts."""
        return frozenset({self.kind})

    @property
    def primary_kind(self) -> str:
        """The kind used when `send` names none."""
        return self.kind

    def _check_kind(self, carrier: str | None) -> None:
        if carrier is not None and carrier != self.kind:
            raise ValueError(f"this is a {self.kind!r} carrier, not {carrier!r}")


class CarrierSet(Carrier):
    """Several carriers under one transport, one delivery thread for all of them.

    The first carrier is the primary: `address` is its address, and `send`
    without a kind uses it. Kinds must be distinct. The inbound queue is
    bounded; when it is full a frame is dropped and counted, as a congested
    UDP socket would drop it -- every protocol above already tolerates loss.
    """

    kind = "set"

    def __init__(self, primary: Carrier, *others: Carrier, queue_size: int = 4096):
        carriers = (primary, *others)
        by_kind: dict[str, Carrier] = {}
        for c in carriers:
            if not isinstance(c, Carrier):
                raise TypeError(f"not a Carrier: {c!r}")
            if isinstance(c, CarrierSet):
                raise TypeError("a CarrierSet cannot contain another CarrierSet")
            if not c.kind or c.kind in by_kind:
                raise ValueError(f"carrier kinds must be distinct and non-empty: {c.kind!r}")
            by_kind[c.kind] = c
        self._primary = primary
        self._by_kind = by_kind
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._thread: threading.Thread | None = None
        self._closed = False
        self.dropped = 0
        self.reliable = all(c.reliable for c in carriers)
        self.max_frame = min(c.max_frame for c in carriers)

    @property
    def address(self) -> tuple:  # type: ignore[override]
        return self._primary.address

    @property
    def kinds(self) -> frozenset[str]:
        return frozenset(self._by_kind)

    @property
    def primary_kind(self) -> str:
        return self._primary.kind

    @property
    def delivery_thread(self) -> threading.Thread | None:
        return self._thread

    def carrier(self, kind: str) -> Carrier | None:
        return self._by_kind.get(kind)

    def addresses(self) -> dict[str, tuple]:
        """Each carrier's local bound address, by kind."""
        return {k: c.address for k, c in self._by_kind.items()}

    def start(self, on_datagram: DatagramFn) -> None:
        if self._thread is not None:
            raise RuntimeError("CarrierSet already started")
        self._thread = threading.Thread(target=self._deliver_loop, args=(on_datagram,),
                                        daemon=True, name="carrier-deliver")
        self._thread.start()
        for kind, c in self._by_kind.items():
            c.start(self._enqueue_from(kind))

    def _enqueue_from(self, kind: str) -> DatagramFn:
        def enqueue(data: bytes, addr: tuple) -> None:
            if self._closed:
                return
            try:
                self._queue.put_nowait((data, (addr[0], addr[1], kind)))
            except queue.Full:
                self.dropped += 1
        return enqueue

    def _deliver_loop(self, on_datagram: DatagramFn) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            data, addr = item
            try:
                on_datagram(data, addr)
            except Exception:  # noqa: BLE001 - one bad frame must never stop delivery
                log.exception("inbound frame handler failed")

    def send(self, host: str, port: int, data: bytes, carrier: str | None = None) -> None:
        c = self._by_kind.get(carrier) if carrier is not None else self._primary
        if c is None:
            raise ValueError(f"no {carrier!r} carrier in this set (have {sorted(self._by_kind)})")
        c.send(host, port, data)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for c in self._by_kind.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001 - close the rest regardless
                log.exception("closing %s carrier failed", c.kind)
        if self._thread is not None:
            # The sentinel goes in even when the queue is full: drain one slot.
            while True:
                try:
                    self._queue.put_nowait(_STOP)
                    break
                except queue.Full:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass
            if threading.current_thread() is not self._thread:
                self._thread.join(timeout=2.0)


class MemoryNetwork:
    """An in-process network of `MemoryCarrier`s, for headless tests.

    Addresses are `("mem", n)`. `block(address)` drops everything to and from
    that address until `unblock` -- a path that is down, as a firewall that
    blocks UDP would make it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._carriers: dict[tuple, MemoryCarrier] = {}
        self._blocked: set[tuple] = set()
        self._next = 1

    def _attach(self, carrier: MemoryCarrier) -> tuple:
        with self._lock:
            address = ("mem", self._next)
            self._next += 1
            self._carriers[address] = carrier
            return address

    def _detach(self, address: tuple) -> None:
        with self._lock:
            self._carriers.pop(address, None)

    def block(self, address: tuple) -> None:
        with self._lock:
            self._blocked.add(tuple(address))

    def unblock(self, address: tuple) -> None:
        with self._lock:
            self._blocked.discard(tuple(address))

    def _route(self, src: tuple, dst: tuple, data: bytes) -> None:
        with self._lock:
            if src in self._blocked or dst in self._blocked:
                return
            target = self._carriers.get(dst)
        if target is not None:
            target._receive(data, src)


class MemoryCarrier(Carrier):
    """A carrier on a `MemoryNetwork`. Delivery runs on the carrier's own
    thread, never on the sender's, so a reply sent from inside a handler cannot
    re-enter the sender. `kind` can be overridden to stand in for a real
    carrier kind in tests."""

    reliable = True
    max_frame = 1 << 20

    def __init__(self, network: MemoryNetwork, *, kind: str = "memory"):
        self.kind = kind
        self._network = network
        self.address = network._attach(self)
        self._inbox: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._closed = False

    def start(self, on_datagram: DatagramFn) -> None:
        if self._thread is not None:
            raise RuntimeError("carrier already started")
        self._thread = threading.Thread(target=self._loop, args=(on_datagram,), daemon=True,
                                        name=f"mem-{self.kind}-{self.address[1]}")
        self._thread.start()

    def _loop(self, on_datagram: DatagramFn) -> None:
        while True:
            item = self._inbox.get()
            if item is _STOP:
                return
            data, src = item
            try:
                on_datagram(data, src)
            except Exception:  # noqa: BLE001 - as with UDP: a bad frame never kills the loop
                log.exception("memory carrier handler failed")

    def _receive(self, data: bytes, src: tuple) -> None:
        if not self._closed:
            self._inbox.put((bytes(data), src))

    def send(self, host: str, port: int, data: bytes, carrier: str | None = None) -> None:
        self._check_kind(carrier)
        if self._closed:
            raise OSError("carrier is closed")
        if len(data) > self.max_frame:
            raise ValueError(f"frame of {len(data)} bytes exceeds {self.max_frame}")
        self._network._route(self.address, (host, port), bytes(data))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._network._detach(self.address)
        self._inbox.put(_STOP)
        if self._thread is not None and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)


class AsyncCarrierHost:
    """One asyncio event loop on a daemon thread, for asyncio-based carriers.

    The carriers that will run here (aiortc, aioquic) are asyncio libraries;
    the transport and everything above it are threaded. This host is the one
    boundary between the two:

      * `submit(coro)` runs a coroutine on the loop from any thread and returns
        a `concurrent.futures.Future`;
      * `call(fn, *args)` runs a plain callable on the loop thread;
      * a carrier on this loop hands inbound frames to its `on_datagram`, which
        under a `CarrierSet` only enqueues -- the upper layers run on the set's
        delivery thread, never here.

    `stop()` cancels whatever is still pending on the loop, stops it and joins
    the thread. It is idempotent, and `submit` after it raises RuntimeError.
    """

    def __init__(self, *, name: str = "carrier-loop"):
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = False

    def start(self) -> AsyncCarrierHost:
        if self._stopped:
            raise RuntimeError("AsyncCarrierHost was stopped; make a new one")
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, daemon=True, name=self._name)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("event loop did not start")
        return self

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        loop.call_soon(self._ready.set)
        try:
            loop.run_forever()
        finally:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._stopped:
            raise RuntimeError("AsyncCarrierHost is not running")
        return self._loop

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def in_loop_thread(self) -> bool:
        return self._thread is not None and threading.current_thread() is self._thread

    def submit(self, coro: Coroutine[Any, Any, Any]) -> concurrent.futures.Future:
        try:
            loop = self.loop
        except RuntimeError:
            coro.close()  # never leave an un-awaited coroutine behind
            raise
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def call(self, fn: Callable[..., Any], *args: Any) -> concurrent.futures.Future:
        """Run `fn(*args)` on the loop thread; the future carries its result."""
        fut: concurrent.futures.Future = concurrent.futures.Future()

        def run() -> None:
            if not fut.set_running_or_notify_cancel():
                return
            try:
                fut.set_result(fn(*args))
            except BaseException as exc:  # noqa: BLE001 - handed to the caller
                fut.set_exception(exc)

        self.loop.call_soon_threadsafe(run)
        return fut

    def stop(self, timeout: float = 5.0) -> None:
        if self._stopped:
            return
        self._stopped = True
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if threading.current_thread() is not thread:
            thread.join(timeout=timeout)

    def __enter__(self) -> AsyncCarrierHost:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
