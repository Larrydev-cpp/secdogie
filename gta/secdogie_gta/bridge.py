"""The socket bridge to the ScriptHookV plugin -- the on-machine half.

The C++ `.asi` plugin (running inside GTA, single-player) listens on a local TCP
port and speaks newline-delimited JSON: it sends a GameState line each tick and
accepts a Command line back. This client is the thin Python side of that. It
can't be unit-tested without the plugin + a running game, so nothing here is in
the test path -- the tested logic is protocol.py (the messages) and driving.py
(the control law); this only moves bytes.
"""
from __future__ import annotations

import socket

from .protocol import Command, GameState, command_to_json, state_from_json


class GtaBridge:
    """Line-delimited JSON over a local TCP socket to the ScriptHookV plugin."""

    def __init__(self, host: str = "127.0.0.1", port: int = 47800, timeout: float = 2.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._buf = b""

    def read_state(self) -> GameState:
        """Block for the next GameState line from the plugin."""
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("GTA bridge closed by the plugin")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return state_from_json(line.decode("utf-8"))

    def send(self, command: Command) -> None:
        self._sock.sendall(command_to_json(command).encode("utf-8") + b"\n")

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> GtaBridge:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
