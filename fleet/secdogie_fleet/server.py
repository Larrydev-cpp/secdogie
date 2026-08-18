"""The coordinator's transport: accept node connections and feed the Coordinator.

coordinator.py is the pure brain (who runs what, what happens when a node dies);
this is the thin part that owns sockets and threads. One thread per connected
node, newline-delimited JSON both ways, and a lock around the brain because
those threads all touch it.

Nodes dial in (see node.py), so the coordinator is the only side that needs a
reachable port -- typically the VM host on its host-only network.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
from collections.abc import Callable

from .coordinator import Coordinator
from .protocol import (
    NODE_KINDS,
    Hello,
    Message,
    ProtocolError,
    Result,
    Status,
    from_json,
    to_json,
)


class FleetServer:
    """Listens for nodes and drives a Coordinator with what they say.

    Thread-safe: `submit`/`stop_task`/`snapshot` may be called from any thread
    (a CLI, a web UI), and each node connection runs on its own thread.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 47810,
        *,
        max_concurrent: int | None = None,
        max_attempts: int = 2,
        logger: logging.Logger | None = None,
        on_event: Callable[[str, Message], None] | None = None,
    ):
        self.log = logger or logging.getLogger("secdogie_fleet.server")
        self._lock = threading.RLock()
        self.coordinator = Coordinator(
            self._send_to, max_concurrent=max_concurrent, max_attempts=max_attempts
        )
        self._conns: dict[str, socket.socket] = {}  # node_id -> its socket
        self._on_event = on_event
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(16)
        self.address = self._sock.getsockname()
        self._stopping = threading.Event()
        self._accept_thread: threading.Thread | None = None

    def serve_forever(self) -> None:
        self.log.info("fleet coordinator listening on %s:%d", *self.address)
        while not self._stopping.is_set():
            try:
                conn, addr = self._sock.accept()
            except OSError:
                break
            threading.Thread(
                target=self._serve_node, args=(conn, addr), daemon=True,
                name=f"fleet-node-{addr[0]}:{addr[1]}",
            ).start()

    def start(self) -> None:
        """Serve on a background thread (for a CLI that also wants a prompt)."""
        self._accept_thread = threading.Thread(
            target=self.serve_forever, daemon=True, name="fleet-accept"
        )
        self._accept_thread.start()

    def shutdown(self) -> None:
        self._stopping.set()
        try:
            self._sock.close()
        except OSError:
            pass
        with self._lock:
            for conn in list(self._conns.values()):
                try:
                    conn.close()
                except OSError:
                    pass
            self._conns.clear()

    def submit(
        self,
        task: str,
        options: dict | None = None,
        *,
        priority: int = 0,
    ) -> str:
        with self._lock:
            t = self.coordinator.submit(task, options, priority=priority)
            self.coordinator.pump()
            return t.task_id

    def stop_task(self, task_id: str) -> bool:
        with self._lock:
            return self.coordinator.stop_task(task_id)

    def pause_task(self, task_id: str, reason: str = "operator") -> bool:
        """Park a task without deleting it. See Coordinator.pause_task."""
        with self._lock:
            ok = self.coordinator.pause_task(task_id, reason=reason)
            self.coordinator.pump()
            return ok

    def resume_task(self, task_id: str) -> bool:
        with self._lock:
            ok = self.coordinator.resume_task(task_id)
            self.coordinator.pump()
            return ok

    def pause_below_priority(self, min_priority: int, reason: str = "quota") -> list[str]:
        """When one API key cannot sustain all heavy jobs, pause lower-priority
        work instead of dropping it."""
        with self._lock:
            ids = self.coordinator.pause_below_priority(min_priority, reason=reason)
            self.coordinator.pump()
            return ids

    def snapshot(self) -> dict:
        """A consistent view of the fleet, for a CLI/UI to render."""
        with self._lock:
            return {
                "nodes": [
                    {"node_id": n.node_id, "label": n.label, "screen": n.screen,
                     "capabilities": list(n.capabilities), "task_id": n.task_id}
                    for n in self.coordinator.nodes.values()
                ],
                "tasks": [
                    {"task_id": t.task_id, "task": t.task, "state": t.state,
                     "priority": t.priority, "pause_reason": t.pause_reason,
                     "node_id": t.node_id, "step": t.step, "detail": t.detail,
                     "code": t.code, "summary": t.summary, "attempts": t.attempts}
                    for t in self.coordinator.tasks.values()
                ],
                "settled": self.coordinator.is_settled(),
                "paused": self.coordinator.paused_count,
            }

    def _send_to(self, node_id: str, msg: Message) -> None:
        """Coordinator -> node. Called with the lock held (from pump/stop)."""
        conn = self._conns.get(node_id)
        if conn is None:
            self.log.warning("cannot reach node %s (no connection)", node_id)
            return
        try:
            conn.sendall((to_json(msg) + "\n").encode("utf-8"))
        except OSError as e:
            self.log.warning("send to %s failed (%s); dropping it", node_id, e)
            self.coordinator.on_node_lost(node_id)
            self._conns.pop(node_id, None)

    def _serve_node(self, conn: socket.socket, addr: tuple) -> None:
        """One node, one thread: read lines until disconnect."""
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        buf = b""
        node_id: str | None = None
        try:
            while not self._stopping.is_set():
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = from_json(line.decode("utf-8"))
                    except (UnicodeDecodeError, ProtocolError, json.JSONDecodeError) as e:
                        self.log.warning("bad message from %s: %s", addr, e)
                        continue
                    if msg.__class__.__name__.lower() not in NODE_KINDS and not isinstance(
                        msg, (Hello, Status, Result)
                    ):
                        self.log.warning("ignoring coordinator-kind from node %s", addr)
                        continue
                    with self._lock:
                        if isinstance(msg, Hello):
                            node_id = msg.node_id
                            self._conns[node_id] = conn
                            self.coordinator.on_hello(msg)
                            self.coordinator.pump()
                        elif isinstance(msg, Status):
                            self.coordinator.on_status(msg)
                        elif isinstance(msg, Result):
                            self.coordinator.on_result(msg)
                            self.coordinator.pump()
                        if self._on_event is not None and node_id is not None:
                            try:
                                self._on_event(node_id, msg)
                            except Exception:
                                self.log.exception("on_event failed")
        finally:
            with self._lock:
                if node_id is not None and self._conns.get(node_id) is conn:
                    self._conns.pop(node_id, None)
                    self.coordinator.on_node_lost(node_id)
                    self.coordinator.pump()
            try:
                conn.close()
            except OSError:
                pass
