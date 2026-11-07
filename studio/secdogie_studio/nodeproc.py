"""The node as Studio's child process.

Studio does not embed the node: it runs ``secdogie-node run`` as a child, the
same program a headless machine runs, and reads its one ready line. The child
lives exactly as long as Studio:

  * Studio holds the child's standard input; the node runs with
    ``--exit-on-stdin-eof``. Closing the window closes the pipe and the node
    stops cleanly. If Studio crashes or is killed, the OS closes the pipe and
    the node stops the same way, on every OS, with no signal involved;
  * ``stop()`` closes the pipe, waits, and only then terminates or kills.

Nothing is detached, daemonized, installed or scheduled.
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass


class NodeError(RuntimeError):
    """The node did not start, or stopped before it was ready."""


@dataclass(frozen=True)
class Ready:
    did: str
    host: str
    port: int

    @property
    def dial_host(self) -> str:
        """Where a local App reaches it: a wildcard listen is dialled on loopback."""
        return "127.0.0.1" if self.host in ("", "0.0.0.0") else self.host


def node_command(args: list[str]) -> list[str]:
    """How to run the node: a frozen Studio build carries the node in the same
    binary (``--node-child``); otherwise it is the installed package."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--node-child", *args]
    return [sys.executable, "-m", "secdogie_node.cli", *args]


class NodeProcess:
    """One child ``secdogie-node``. Thread-safe for the GUI's polling."""

    def __init__(self, args: list[str], *, env: dict | None = None, log_lines: int = 500):
        self._cmd = node_command(args)
        self._env = dict(os.environ if env is None else env, PYTHONUNBUFFERED="1")
        self._proc: subprocess.Popen | None = None
        self._ready: Ready | None = None
        self._ready_event = threading.Event()
        self._stdout_closed = False
        self._log: collections.deque[str] = collections.deque(maxlen=log_lines)
        self._lock = threading.Lock()
        self._readers: list[threading.Thread] = []

    @property
    def command(self) -> list[str]:
        return list(self._cmd)

    def start(self) -> None:
        if self._proc is not None:
            raise NodeError("the node was already started")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # no console window on Windows
        self._proc = subprocess.Popen(
            self._cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self._env, creationflags=flags, text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self._readers = [
            threading.Thread(target=self._read_stdout, daemon=True, name="node-stdout"),
            threading.Thread(target=self._read_stderr, daemon=True, name="node-stderr"),
        ]
        for t in self._readers:
            t.start()

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.rstrip("\n")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                msg = None
            if isinstance(msg, dict) and msg.get("event") == "ready" and self._ready is None:
                host, _, port = str(msg.get("listen", "")).rpartition(":")
                if isinstance(msg.get("did"), str) and port.isdigit():
                    with self._lock:
                        self._ready = Ready(msg["did"], host, int(port))
                    self._ready_event.set()
                    self._note(f"ready: {msg['did']} on {msg['listen']}")
                    continue
            self._note(line)
        self._stdout_closed = True
        self._ready_event.set()  # stdout closed: wake anyone waiting

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            self._note(line.rstrip("\n"))

    def _note(self, line: str) -> None:
        with self._lock:
            self._log.append(line)

    @property
    def ready(self) -> Ready | None:
        with self._lock:
            return self._ready

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def returncode(self) -> int | None:
        return self._proc.poll() if self._proc is not None else None

    def log_tail(self, n: int = 50) -> list[str]:
        with self._lock:
            return list(self._log)[-n:]

    def wait_ready(self, timeout: float = 30.0) -> Ready:
        if self._proc is None:
            raise NodeError("the node was not started")
        self._ready_event.wait(timeout)
        ready = self.ready
        if ready is not None:
            return ready
        if self._stdout_closed and self._proc.poll() is None:
            # The pipe's end of file and the exit status do not arrive together
            # (on Windows the gap is visible): a node that closed its output
            # without a ready line is on its way out, so let it finish exiting
            # and report why, rather than a misleading timeout.
            try:
                self._proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                pass
        if self._proc.poll() is not None:
            for t in self._readers:
                t.join(timeout=2.0)  # collect what it said before it went
            tail = "\n".join(self.log_tail(20))
            raise NodeError(f"the node exited with code {self._proc.returncode} before it was ready:\n{tail}")
        raise NodeError(f"the node was not ready within {timeout:g} s")

    def stop(self, timeout: float = 10.0) -> int | None:
        """Close the node's standard input (it stops cleanly), wait; terminate
        only if it ignored that, and kill only if it ignored both."""
        proc = self._proc
        if proc is None:
            return None
        if proc.poll() is None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        for t in self._readers:
            t.join(timeout=2.0)
        return proc.returncode

    def __enter__(self) -> NodeProcess:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def wait_until(pred, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())
