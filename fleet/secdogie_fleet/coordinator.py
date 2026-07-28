"""The scheduling brain: which task runs on which node, and what happens when
a node dies mid-task.

Pure and I/O-free on purpose. Every outbound message goes through a `send`
callback the caller supplies, and time comes from an injected clock, so the
whole lifecycle -- register, dispatch, progress, finish, drop, requeue, cap --
is provable in unit tests with no sockets, no VMs and no desktops. `server.py`
is the thin part that actually moves bytes.

The model is deliberately small: tasks queue up, each idle node gets at most
one, and a node that drops hands its task back to the queue so another node
picks it up. A task that has been retried too many times is failed rather than
bounced around the fleet forever.
"""
from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .protocol import Assign, Hello, Message, Result, Status, Stop

# Task lifecycle.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

# The agent loop's exit codes that mean "the task itself is finished", as
# opposed to an infrastructure failure worth retrying elsewhere. 0 = done,
# 2 = the user declined, 3 = steps/plan exhausted, 5 = stopped on request.
_TERMINAL_CODES = frozenset({0, 2, 3, 5})


@dataclass
class Task:
    task_id: str
    task: str
    options: dict = field(default_factory=dict)
    state: str = QUEUED
    node_id: str | None = None  # which node holds it while RUNNING
    attempts: int = 0
    # Nodes that have already had a go at this task. A retry prefers a desktop
    # that hasn't tried yet: if the failure was the node's fault (no display,
    # a wedged VM), running it again on the same box just fails again.
    tried_nodes: set[str] = field(default_factory=set)
    code: int | None = None  # the agent exit code, once finished
    summary: str = ""
    step: int = 0
    detail: str = ""


@dataclass
class Node:
    node_id: str
    label: str = ""
    screen: tuple[int, int] | None = None
    capabilities: tuple[str, ...] = ()
    task_id: str | None = None  # the assignment it currently holds
    last_seen: float = 0.0

    @property
    def busy(self) -> bool:
        return self.task_id is not None


class Coordinator:
    """Tracks nodes and tasks and decides what to dispatch.

    `send(node_id, message)` is how it talks to a node; the caller wires that to
    a real socket (or a list, in tests). `max_concurrent` caps how many tasks run
    at once across the whole fleet even if more nodes are free -- the fleet's
    ceiling is usually the shared API quota, not the number of desktops.
    """

    def __init__(
        self,
        send: Callable[[str, Message], None],
        *,
        max_concurrent: int | None = None,
        max_attempts: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._send = send
        self.max_concurrent = max_concurrent
        self.max_attempts = max_attempts
        self._clock = clock
        self.nodes: dict[str, Node] = {}
        self.tasks: dict[str, Task] = {}
        self._order: list[str] = []  # task ids, FIFO
        self._ids = itertools.count(1)

    # -- task intake ----------------------------------------------------------

    def submit(self, task: str, options: dict | None = None, *, task_id: str | None = None) -> Task:
        """Queue a task. Dispatch happens on the next `pump()`."""
        tid = task_id or f"t{next(self._ids)}"
        if tid in self.tasks:
            raise ValueError(f"task id {tid!r} already exists")
        t = Task(task_id=tid, task=task, options=dict(options or {}))
        self.tasks[tid] = t
        self._order.append(tid)
        return t

    # -- node events ----------------------------------------------------------

    def on_hello(self, hello: Hello) -> Node:
        """Register a node, or re-register one that reconnected. A returning
        node is assumed to have lost whatever it was running (its process
        restarted), so that task goes back in the queue."""
        existing = self.nodes.get(hello.node_id)
        if existing is not None and existing.task_id is not None:
            self._requeue(existing.task_id, "node reconnected mid-task")
        node = Node(
            node_id=hello.node_id,
            label=hello.label,
            screen=hello.screen,
            capabilities=hello.capabilities,
            last_seen=self._clock(),
        )
        self.nodes[hello.node_id] = node
        return node

    def on_status(self, status: Status) -> None:
        """Record progress. Unknown nodes are ignored rather than raising -- a
        late message from a node we already dropped is not an error."""
        node = self.nodes.get(status.node_id)
        if node is None:
            return
        node.last_seen = self._clock()
        if status.task_id and status.task_id in self.tasks:
            t = self.tasks[status.task_id]
            t.step = status.step
            t.detail = status.detail

    def on_result(self, result: Result) -> None:
        """Finish an assignment. A task that failed for an infrastructure reason
        (an exit code outside _TERMINAL_CODES) goes back to the queue for
        another node to try, until max_attempts."""
        node = self.nodes.get(result.node_id)
        if node is not None:
            node.last_seen = self._clock()
            if node.task_id == result.task_id:
                node.task_id = None
        t = self.tasks.get(result.task_id)
        if t is None or t.state in (DONE, FAILED):
            return
        t.code = result.code
        t.summary = result.summary
        if result.code in _TERMINAL_CODES:
            t.state = DONE
            t.node_id = None
        elif t.attempts >= self.max_attempts:
            t.state = FAILED
            t.node_id = None
        else:
            self._requeue(result.task_id, f"exit code {result.code}")

    def on_node_lost(self, node_id: str) -> None:
        """A node's connection dropped. Its task returns to the queue so another
        desktop can pick it up -- the point of a fleet is that one dead VM
        doesn't take the work with it."""
        node = self.nodes.pop(node_id, None)
        if node is not None and node.task_id is not None:
            self._requeue(node.task_id, "node lost")

    # -- dispatch -------------------------------------------------------------

    def pump(self) -> int:
        """Hand queued tasks to idle nodes, up to the concurrency cap. Returns
        how many were dispatched. Call after submitting or on any node event."""
        dispatched = 0
        for tid in list(self._order):
            t = self.tasks[tid]
            if t.state != QUEUED:
                continue
            if self.max_concurrent is not None and self.running_count >= self.max_concurrent:
                break
            node = self._idle_node(t)
            if node is None:
                break
            t.state = RUNNING
            t.node_id = node.node_id
            t.attempts += 1
            t.tried_nodes.add(node.node_id)
            node.task_id = tid
            self._order.remove(tid)
            self._send(node.node_id, Assign(task_id=tid, task=t.task, options=t.options))
            dispatched += 1
        return dispatched

    def stop_task(self, task_id: str) -> bool:
        """Ask whichever node holds `task_id` to abandon it. The task is only
        finished once that node reports back, so state stays honest."""
        t = self.tasks.get(task_id)
        if t is None or t.state != RUNNING or t.node_id is None:
            return False
        self._send(t.node_id, Stop(task_id=task_id))
        return True

    # -- views ----------------------------------------------------------------

    @property
    def running_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t.state == RUNNING)

    @property
    def queued_count(self) -> int:
        return len(self._order)

    def idle_nodes(self) -> list[Node]:
        return [n for n in self.nodes.values() if not n.busy]

    def is_settled(self) -> bool:
        """True once nothing is queued or running -- the fleet has no work left."""
        return self.queued_count == 0 and self.running_count == 0

    # -- internals ------------------------------------------------------------

    def _idle_node(self, task: Task) -> Node | None:
        """An idle node for `task`, preferring one that hasn't already tried it
        -- a retry belongs on a different desktop where possible. Falls back to
        any idle node so a one-node fleet can still retry."""
        idle = [n for n in self.nodes.values() if not n.busy]
        if not idle:
            return None
        for node in idle:
            if node.node_id not in task.tried_nodes:
                return node
        return idle[0]

    def _requeue(self, task_id: str, reason: str) -> None:
        t = self.tasks.get(task_id)
        if t is None or t.state in (DONE, FAILED):
            return
        if t.node_id is not None:
            node = self.nodes.get(t.node_id)
            if node is not None and node.task_id == task_id:
                node.task_id = None
        t.node_id = None
        if t.attempts >= self.max_attempts:
            t.state = FAILED
            t.detail = f"gave up after {t.attempts} attempt(s): {reason}"
            return
        t.state = QUEUED
        t.detail = f"requeued: {reason}"
        if task_id not in self._order:
            self._order.insert(0, task_id)  # retry ahead of untried work
