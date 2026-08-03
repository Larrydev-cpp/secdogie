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

    # -- lifecycle ------------------------------------------------------------

    def serve_forever(self) -> None:
        self.log.info("fleet coordinator listening on %s:%d", *self.address)
        while not self._stopping.is_set():
            try:
                conn, addr = self._sock.accept()
            except OSError:
                break  # socket closed by shutdown()
            threading.Thread(
                target=self._serve_node, args=(conn, addr), daemon=True,
                name=f"fleet-node-{addr[0]}:{addr[1]}",
            ).start()

    def start(self) -> None:
        """Serve on a background thread (for a CLI that also wants a prompt)."""
        self._accept_thread = threading.Thread(target=self.serve_forever, daemon=True, name="fleet-accept")
        self._accept_thread.start()

    def shutdown(self) -> None:
        self._stopping.set()
        try:
            self._sock.close()
        except OSError:
            pass
        with self._lock:
            conns = list(self._conns.values())
        for c in conns:
            try:
                c.close()
            except OSError:
                pass

    # -- public API (thread-safe) ---------------------------------------------

    def submit(self, task: str, options: dict | None = None) -> str:
        with self._lock:
            t = self.coordinator.submit(task, options)
            self.coordinator.pump()
            return t.task_id

    def stop_task(self, task_id: str) -> bool:
        with self._lock:
            return self.coordinator.stop_task(task_id)

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
                     "node_id": t.node_id, "step": t.step, "detail": t.detail,
                     "code": t.code, "summary": t.summary, "attempts": t.attempts}
                    for t in self.coordinator.tasks.values()
                ],
                "settled": self.coordinator.is_settled(),
            }

    # -- internals ------------------------------------------------------------

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

    def _serve_node(self, conn: socket.socket, addr) -> None:
        """One node's connection: read messages until it goes away, then make
        sure the coordinator learns it's gone so its task is requeued."""
        node_id: str | None = None
        buf = b""
        # A guest that crashes closes its socket and we see recv() return empty.
        # A guest that dies *silently* -- paused VM, blackholed network -- never
        # closes anything, so without this the read would block forever and the
        # task would never be requeued. Keepalive turns that into an eventual
        # connection error.
        try:
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    if not raw.strip():
                        continue
                    try:
                        msg = from_json(raw.decode("utf-8"), expect=NODE_KINDS)
                    except (ProtocolError, UnicodeDecodeError, json.JSONDecodeError) as e:
                        self.log.warning("ignoring bad message from %s: %s", addr, e)
                        continue
                    node_id = self._apply(msg, conn) or node_id
        except OSError:
            pass
        finally:
            if node_id is not None:
                with self._lock:
                    self._conns.pop(node_id, None)
                    self.coordinator.on_node_lost(node_id)
                    self.coordinator.pump()  # its task goes to another desktop
                self.log.info("node %s disconnected", node_id)
            try:
                conn.close()
            except OSError:
                pass

    def _apply(self, msg: Message, conn: socket.socket) -> str | None:
        """Feed one node message to the brain. Returns the node id if this
        message identified the connection."""
        with self._lock:
            if isinstance(msg, Hello):
                self._conns[msg.node_id] = conn
                node = self.coordinator.on_hello(msg)
                self.log.info("node %s (%s) joined", node.node_id, node.label or "unlabelled")
                self.coordinator.pump()
                self._emit("hello", msg)
                return msg.node_id
            if isinstance(msg, Status):
                self.coordinator.on_status(msg)
                self._emit("status", msg)
            elif isinstance(msg, Result):
                self.coordinator.on_result(msg)
                self.log.info("task %s finished on %s: code=%d %s",
                              msg.task_id, msg.node_id, msg.code, msg.summary)
                self.coordinator.pump()  # that desktop is free now
                self._emit("result", msg)
        return None

    def _emit(self, kind: str, msg: Message) -> None:
        if self._on_event is not None:
            try:
                self._on_event(kind, msg)
            except Exception:
                self.log.debug("on_event callback raised", exc_info=True)
