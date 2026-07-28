"""The node half: run inside one isolated desktop and do what the coordinator says.

A node is a full secdogie-agent that happens to take its task over a socket
instead of the command line. It lives inside its own Windows VM or user session,
so it owns that desktop's mouse, keyboard and foreground window outright -- no
input lock shared with anyone, no focus stealing between tasks. That is the whole
point of the fleet: N of these act at the same time, where N windows on one
desktop can only take turns.

It **dials out** to the coordinator rather than listening, so guests behind NAT
need no inbound firewall rule.

Each node resolves its **own** API key from its own environment/config (via the
agent's normal resolution), so a fleet naturally spreads load across keys instead
of funnelling every desktop through one quota.

The task-running seam is injected (`run_task`), so the receive/dispatch logic is
testable without a desktop, a model, or a network.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import uuid
from collections.abc import Callable

from .protocol import (
    COORDINATOR_KINDS,
    Assign,
    Hello,
    Message,
    ProtocolError,
    Result,
    Status,
    Stop,
    from_json,
    to_json,
)

# AgentConfig fields a coordinator is allowed to set through `assign.options`.
# An allowlist, not a passthrough: an assignment must not be able to reach
# arbitrary constructor arguments (or point the node at another machine's files).
ALLOWED_OPTIONS = frozenset({
    "auto", "dry_run", "max_steps", "desktop_ax", "plan", "watch",
    "grid", "max_image_edge", "action_pause", "verify_actions",
    "stall_limit", "max_transient_retries", "subtask_step_limit",
})


def default_node_id() -> str:
    """Stable-ish per machine: the hostname plus a short random suffix, so two
    VMs cloned from the same image don't collide."""
    try:
        host = socket.gethostname()
    except Exception:
        host = "node"
    return f"{host}-{uuid.uuid4().hex[:6]}"


def filter_options(options: dict) -> dict:
    """Keep only the AgentConfig fields a coordinator may set (ALLOWED_OPTIONS).
    Unknown or unsafe keys are dropped rather than raising -- a newer coordinator
    talking to an older node should still get a run, just without the flag it
    doesn't understand."""
    return {k: v for k, v in options.items() if k in ALLOWED_OPTIONS}


def local_screen_size() -> tuple[int, int] | None:
    """This desktop's size for the `hello`, or None if there's no display (the
    node still registers -- the coordinator can then see it's not usable)."""
    try:
        from secdogie_agent import screen

        return screen.primary_size()
    except Exception:
        return None


def local_capabilities() -> tuple[str, ...]:
    """What this node's OS/libraries actually support, so a coordinator can see
    which desktops can take an element-aware (`desktop_ax`) assignment."""
    caps: list[str] = []
    try:
        from secdogie_agent import desktop_ax

        if desktop_ax.make_desktop_ax_provider() is not None:
            caps.append("desktop-ax")
    except Exception:
        pass
    return tuple(caps)


class Node:
    """Receives assignments and runs them on this machine, one at a time.

    `run_task(task, options, should_stop, on_progress) -> (code, summary)` is the
    seam: production wires it to `run_agent_task` below; tests pass a fake.
    """

    def __init__(
        self,
        node_id: str,
        send: Callable[[Message], None],
        run_task: Callable[..., tuple[int, str]],
        *,
        label: str = "",
        screen: tuple[int, int] | None = None,
        capabilities: tuple[str, ...] = (),
        logger: logging.Logger | None = None,
    ):
        self.node_id = node_id
        self._send = send
        self._run_task = run_task
        self.label = label
        self.screen = screen
        self.capabilities = capabilities
        self.log = logger or logging.getLogger("secdogie_fleet.node")
        self.current_task: str | None = None
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    def hello(self) -> None:
        """Announce ourselves. Sent on connect and on every reconnect."""
        self._send(Hello(
            node_id=self.node_id, label=self.label,
            screen=self.screen, capabilities=self.capabilities,
        ))

    def handle(self, msg: Message) -> None:
        """Dispatch one coordinator message."""
        if isinstance(msg, Assign):
            self._start(msg)
        elif isinstance(msg, Stop):
            self._request_stop(msg)

    def _start(self, assign: Assign) -> None:
        if self.current_task is not None:
            # Busy: tell the coordinator rather than silently dropping the task,
            # so it can put it back in the queue for another desktop.
            self._send(Result(
                node_id=self.node_id, task_id=assign.task_id, code=7,
                summary=f"node busy with {self.current_task}",
            ))
            return
        self.current_task = assign.task_id
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._run, args=(assign,), name=f"fleet-task-{assign.task_id}", daemon=True,
        )
        self._worker.start()

    def _request_stop(self, stop: Stop) -> None:
        if self.current_task is None:
            return
        if stop.task_id is not None and stop.task_id != self.current_task:
            return  # a stop for something we're not running
        self._stop.set()

    def _run(self, assign: Assign) -> None:
        """The worker body: run the task, then always report a terminal result --
        including on an unexpected crash, so the coordinator never waits forever
        on a node whose task blew up."""
        self._send(Status(node_id=self.node_id, state="running", task_id=assign.task_id, detail="starting"))

        def on_progress(step: int, detail: str) -> None:
            self._send(Status(
                node_id=self.node_id, state="running",
                task_id=assign.task_id, step=step, detail=detail,
            ))

        try:
            code, summary = self._run_task(
                task=assign.task,
                options=filter_options(assign.options),
                should_stop=self._stop.is_set,
                on_progress=on_progress,
            )
        except Exception as e:
            self.log.exception("task %s crashed", assign.task_id)
            code, summary = 1, f"node error: {e}"
        finally:
            self.current_task = None

        self._send(Result(node_id=self.node_id, task_id=assign.task_id, code=code, summary=summary))
        self._send(Status(node_id=self.node_id, state="idle", task_id=None))

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until the current task finishes. Returns True if idle."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)
        return self.current_task is None


def run_agent_task(task: str, options: dict, should_stop, on_progress) -> tuple[int, str]:
    """Production task runner: the real agent loop against this machine's desktop.

    Resolves this node's OWN provider/key through the agent's normal config
    resolution -- which is what spreads a fleet's load across several keys
    instead of one. Returns (exit code, summary).
    """
    import argparse

    from secdogie_agent import cli_common
    from secdogie_agent.loop import AgentConfig, run

    # cli_common works off an argparse Namespace; build one with the defaults the
    # agent CLI would produce, then apply the (already filtered) options.
    args = argparse.Namespace(
        api_key=None, model=None, config=None, provider=None,
        auto=True, dry_run=False, allow_risky=False, max_steps=40,
        log_file=None, max_image_edge=None, grid=False, action_pause=None,
        no_verify=False, stall_limit=None, plan=False, watch=False,
        watch_interval=None, trace=None, memory=None, subtask_step_limit=None,
    )
    provider = cli_common.resolve_provider(args, "secdogie-fleet")
    if provider is None:
        return 1, "no API key resolved on this node (set one in its own env/config)"

    cfg_kwargs = cli_common.loop_config_kwargs(args, task=task, backend=None)
    cfg_kwargs.update(options)
    cfg_kwargs["should_stop"] = should_stop
    code = run(provider, AgentConfig(**cfg_kwargs))
    return code, f"agent exited {code}"


def connect_and_serve(
    host: str,
    port: int,
    *,
    node_id: str | None = None,
    label: str = "",
    run_task: Callable[..., tuple[int, str]] = run_agent_task,
    logger: logging.Logger | None = None,
) -> None:
    """Dial the coordinator and serve assignments until the socket closes.

    One connection, newline-delimited JSON both ways. Returns when the
    coordinator disconnects; the CLI wraps this in a reconnect loop.
    """
    log = logger or logging.getLogger("secdogie_fleet.node")
    nid = node_id or default_node_id()

    with socket.create_connection((host, port)) as sock:
        # So a coordinator that dies silently (host sleeps, network blackholes)
        # eventually surfaces as a connection error instead of this node blocking
        # on recv forever; the CLI's reconnect loop then dials back in.
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        lock = threading.Lock()

        def send(msg: Message) -> None:
            line = (to_json(msg) + "\n").encode("utf-8")
            with lock:  # the worker thread and the main loop both send
                sock.sendall(line)

        node = Node(
            nid, send, run_task,
            label=label, screen=local_screen_size(),
            capabilities=local_capabilities(), logger=log,
        )
        node.hello()
        log.info("registered with %s:%d as %s", host, port, nid)

        buf = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                log.info("coordinator closed the connection")
                return
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                if not raw.strip():
                    continue
                try:
                    msg = from_json(raw.decode("utf-8"), expect=COORDINATOR_KINDS)
                except (ProtocolError, UnicodeDecodeError, json.JSONDecodeError) as e:
                    log.warning("ignoring bad message from coordinator: %s", e)
                    continue
                node.handle(msg)
