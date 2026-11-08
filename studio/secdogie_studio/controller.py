"""What the Studio window does, without the window: GUI-free and testable.

The Qt view (app.py) calls these methods and polls ``snapshot()`` on a timer.
Slow work (the first-run key derivation, the link check) runs on a worker
thread and lands in the snapshot, so the view never calls Qt from a thread.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from .link import check_link
from .nodeproc import NodeError, NodeProcess
from .profile import DEFAULT_ROLES, Profile, ProfileError, create_profile

# Node states, as the view shows them.
STOPPED, STARTING, RUNNING, EXITED = "stopped", "starting", "running", "exited"


@dataclass(frozen=True)
class Snapshot:
    initialized: bool
    busy: str = ""                 # what a worker thread is doing, if anything
    error: str = ""                # the last thing that went wrong
    node_state: str = STOPPED
    node_did: str = ""
    app_did: str = ""
    operator_did: str = ""
    roles: tuple[str, ...] = ()
    listen: str = ""
    pid: int | None = None
    returncode: int | None = None
    link: str = ""                 # the node's reply to the last link check
    log: tuple[str, ...] = field(default_factory=tuple)


class StudioController:
    def __init__(self, root: str | Path, *, kdf: str = "moderate", node_factory=NodeProcess):
        self.profile = Profile(Path(root))
        self._kdf = kdf
        self._node_factory = node_factory
        self._node: NodeProcess | None = None
        self._lock = threading.Lock()
        self._busy = ""
        self._error = ""
        self._link = ""
        self._workers: list[threading.Thread] = []
        self._ids: tuple[str, str, str] | None = None  # node, App, operator DIDs, read once

    # -- worker plumbing ----------------------------------------------------------

    def _run(self, what: str, fn) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy, self._error = what, ""

        def work():
            try:
                fn()
            except (ProfileError, NodeError, OSError, ValueError, RuntimeError) as e:
                with self._lock:
                    self._error = str(e)
            finally:
                with self._lock:
                    self._busy = ""

        t = threading.Thread(target=work, daemon=True, name=f"studio-{what}")
        self._workers.append(t)
        t.start()
        return True

    def wait_idle(self, timeout: float = 60.0) -> None:
        for t in list(self._workers):
            t.join(timeout)

    # -- first run --------------------------------------------------------------

    def needs_first_run(self) -> bool:
        return not self.profile.initialized

    def create_profile(self, passphrase: str, *, roles=DEFAULT_ROLES, name: str = "") -> bool:
        """Start creating the profile on a worker (Argon2id takes a moment)."""
        secret = passphrase.encode("utf-8")
        return self._run("creating keys", lambda: create_profile(self.profile.root, secret, roles=roles,
                                                                 kdf=self._kdf, name=name))

    # -- the node ---------------------------------------------------------------

    def start_node(self) -> None:
        with self._lock:
            if self._node is not None and self._node.running:
                return
            self._error = ""
            self._link = ""
        try:
            node = self._node_factory(self.profile.node_args())
            node.start()
        except (ProfileError, NodeError, OSError) as e:
            with self._lock:
                self._error = str(e)
            return
        with self._lock:
            self._node = node

    def stop_node(self, timeout: float = 10.0) -> int | None:
        with self._lock:
            node = self._node
        return node.stop(timeout) if node is not None else None

    def ready(self):
        """The running node's ready line (``nodeproc.Ready``), or None."""
        with self._lock:
            node = self._node
        return node.ready if node is not None and node.running else None

    def check_link(self) -> bool:
        with self._lock:
            node = self._node
        ready = node.ready if node is not None and node.running else None
        if ready is None:
            with self._lock:
                self._error = "the node is not running"
            return False

        def work():
            reply = check_link(self.profile, ready)
            with self._lock:
                self._link = reply

        return self._run("checking the link", work)

    def shutdown(self, timeout: float = 10.0) -> int | None:
        """The window is closing: the node stops with it."""
        code = self.stop_node(timeout)
        self.wait_idle(timeout)
        return code

    # -- what the view shows ------------------------------------------------------

    def snapshot(self) -> Snapshot:
        with self._lock:
            node, busy, error, link = self._node, self._busy, self._error, self._link
        initialized = self.profile.initialized
        if not initialized:
            return Snapshot(False, busy=busy, error=error)
        try:
            settings = self.profile.settings()
            if self._ids is None:
                self._ids = (self.profile.node_did(), self.profile.app_did(), self.profile.operator_did())
            ids = self._ids
        except (ProfileError, OSError, ValueError) as e:
            return Snapshot(True, busy=busy, error=error or str(e))
        state, pid, code, log, listen = STOPPED, None, None, (), settings.get("listen", "")
        if node is not None:
            pid, log = node.pid, tuple(node.log_tail(200))
            if node.running:
                ready = node.ready
                state = RUNNING if ready is not None else STARTING
                if ready is not None:
                    listen = f"{ready.host}:{ready.port}"
            else:
                state, code = EXITED, node.returncode
        return Snapshot(True, busy=busy, error=error, node_state=state, node_did=ids[0], app_did=ids[1],
                        operator_did=ids[2], roles=tuple(settings.get("roles", ())), listen=listen, pid=pid,
                        returncode=code, link=link, log=log)
