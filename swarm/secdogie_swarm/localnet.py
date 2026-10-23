"""A real asyncio TCP transport for running the core and nodes as separate
processes (or tasks) over actual sockets.

This is the local stand-in for the production WebRTC data channel: the bytes on
the wire are the same canonical-JSON `wire` messages, framed with a 4-byte
big-endian length prefix. It exists so the swarm can actually *run* end to end --
a core listening, nodes connecting, real frames crossing a socket -- not just be
unit-tested in memory. The production transport swaps in without touching
`service.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from collections.abc import Callable

_LEN = struct.Struct(">I")
MAX_FRAME = 1 << 20  # 1 MiB; a wire message is far smaller


async def _read_frame(reader: asyncio.StreamReader) -> bytes | None:
    header = await reader.readexactly(4)
    (n,) = _LEN.unpack(header)
    if n > MAX_FRAME:
        raise ValueError("frame too large")
    return await reader.readexactly(n)


def _frame(data: bytes) -> bytes:
    return _LEN.pack(len(data)) + data


class CoreServer:
    """The core's listening end. Each accepted connection gets an integer id."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        on_message: Callable[[int, bytes], None] | None = None,
        on_disconnect: Callable[[int], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self._server: asyncio.AbstractServer | None = None
        self._writers: dict[int, asyncio.StreamWriter] = {}
        self._next = 1

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = self._next
        self._next += 1
        self._writers[conn] = writer
        try:
            while True:
                data = await _read_frame(reader)
                if data is None:
                    break
                if self.on_message:
                    self.on_message(conn, data)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            self._writers.pop(conn, None)
            with contextlib.suppress(Exception):
                writer.close()
            if self.on_disconnect:
                self.on_disconnect(conn)

    def send(self, conn_id: int, data: bytes) -> None:
        writer = self._writers.get(conn_id)
        if writer is not None and not writer.is_closing():
            writer.write(_frame(data))

    async def stop(self) -> None:
        for w in list(self._writers.values()):
            with contextlib.suppress(Exception):
                w.close()
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


class NodeClient:
    """A node's single connection to the core."""

    def __init__(
        self, host: str, port: int, *, on_message: Callable[[bytes], None] | None = None
    ) -> None:
        self.host = host
        self.port = port
        self.on_message = on_message
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        assert self._reader is not None
        try:
            while True:
                data = await _read_frame(self._reader)
                if data is None:
                    break
                if self.on_message:
                    self.on_message(data)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass

    def send(self, data: bytes) -> None:
        if self._writer is not None and not self._writer.is_closing():
            self._writer.write(_frame(data))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
