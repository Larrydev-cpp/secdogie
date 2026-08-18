"""The scheduling brain: which task runs on which node, and what happens when
a node dies mid-task.

Pure and I/O-free on purpose. Every outbound message goes through a `send`
callback the caller supplies, and time comes from an injected clock, so the
whole lifecycle -- register, dispatch, progress, finish, drop, requeue, cap --
is provable in unit tests with no sockets, no VMs and no desktops. `server.py`
is the thin part that actually moves bytes.

The model is deliberately small: tasks queue up (ordered by priority), each
idle node gets at most one, and a node that drops hands its task back to the
queue so another node picks it up. A task that has been retried too many times
is failed rather than bounced around the fleet forever.

When the shared API quota (or `--max-concurrent`) is the bottleneck, lower-
priority work is **paused** — kept in the registry, not deleted — and can be
resumed later without resubmitting.
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
PAUSED = "paused"  # held without a node; not deleted — resume() puts it back
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
    # Higher runs first. Equal priorities keep submission order (stable).
    priority: int = 0
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
    pause_reason: str = ""  # why it was paused (quota, operator, ...)


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
    at once across the fleet — the real ceiling is usually shared API quota, not
    the number of VMs. Excess work stays queued or can be paused (not deleted).
    """

    def __init__(
        self,
        send: Callable[[str, Message], None],
        *,
        max_concurrent: int | None = None,
        max_attempts: int = 2,
        clock: Callable[[], float] | None = None,
    ):
        self._send = send
        self.max_concurrent = max_concurrent
        self.max_attempts = max_attempts
        self._clock = clock or time.monotonic
        self.nodes: dict[str, Node] = {}
        self.tasks: dict[str, Task] = {}
        # task ids waiting to run, highest priority first (stable for ties)
        self._order: list[str] = []
        self._ids = itertools.count(1)

    def submit(
        self,
        task: str,
        options: dict | None = None,
        *,
        task_id: str | None = None,
        priority: int = 0,
    ) -> Task:
        tid = task_id or f"t{next(self._ids)}"
        if tid in self.tasks:
            raise ValueError(f"task id {tid!r} already exists")
        t = Task(
            task_id=tid,
            task=task,
            options=dict(options or {}),
            priority=int(priority),
            state=QUEUED,
        )
        self.tasks[tid] = t
        self._enqueue(tid)
        return t

    def pause_task(self, task_id: str, reason: str = "operator") -> bool:
        """Hold a task without deleting it.

        - QUEUED: leave the registry, drop from the dispatch order.
        - RUNNING: ask the node to stop; when the result arrives, mark PAUSED
          instead of DONE/requeue (see on_result).
        - Already PAUSED: update reason, still True.
        """
        t = self.tasks.get(task_id)
        if t is None or t.state in (DONE, FAILED):
            return False
        if t.state == PAUSED:
            t.pause_reason = reason or t.pause_reason
            return True
        if t.state == QUEUED:
            if task_id in self._order:
                self._order.remove(task_id)
            t.state = PAUSED
            t.pause_reason = reason
            return True
        if t.state == RUNNING and t.node_id is not None:
            t.pause_reason = reason or "operator"
            t.detail = f"pause requested ({t.pause_reason})"
            self._send(t.node_id, Stop(task_id=task_id))
            return True
        return False

    def resume_task(self, task_id: str) -> bool:
        """Move a PAUSED task back to the queue (by priority). Does not delete."""
        t = self.tasks.get(task_id)
        if t is None or t.state != PAUSED:
            return False
        t.state = QUEUED
        t.pause_reason = ""
        t.node_id = None
        self._enqueue(task_id)
        return True

    def pause_below_priority(self, min_priority: int, reason: str = "quota") -> list[str]:
        """Pause every QUEUED/RUNNING task with priority strictly below
        `min_priority`. Used when one API key cannot feed all heavy jobs:
        keep high-priority work, park the rest without deleting.

        Returns the task ids that were paused.
        """
        paused: list[str] = []
        for tid, t in list(self.tasks.items()):
            if t.priority >= min_priority:
                continue
            if t.state in (QUEUED, RUNNING):
                if self.pause_task(tid, reason=reason):
                    paused.append(tid)
        return paused

    def on_hello(self, hello: Hello) -> Node:
        existing = self.nodes.get(hello.node_id)
        if existing is not None and existing.task_id is not None:
            self._requeue(existing.task_id, "node reconnected mid-task")
        node = Node(
            node_id=hello.node_id,
            label=hello.label,
            screen=hello.screen,
            capabilities=tuple(hello.capabilities),
            last_seen=self._clock(),
        )
        self.nodes[hello.node_id] = node
        return node

    def on_status(self, status: Status) -> None:
        node = self.nodes.get(status.node_id)
        if node is None:
            return
        node.last_seen = self._clock()
        if status.task_id and status.task_id in self.tasks:
            t = self.tasks[status.task_id]
            t.step = status.step
            t.detail = status.detail

    def on_result(self, result: Result) -> None:
        """A node finished (or was stopped). If the operator had requested a
        pause, park the task as PAUSED instead of DONE / requeue / delete."""
        node = self.nodes.get(result.node_id)
        if node is not None:
            node.last_seen = self._clock()
            if node.task_id == result.task_id:
                node.task_id = None
        t = self.tasks.get(result.task_id)
        if t is None or t.state in (DONE, FAILED, PAUSED):
            return
        t.code = result.code
        t.summary = result.summary
        t.node_id = None

        if t.pause_reason and result.code == 5:
            t.state = PAUSED
            t.detail = f"paused ({t.pause_reason})"
            return
        if t.pause_reason and result.code not in _TERMINAL_CODES:
            t.state = PAUSED
            t.detail = f"paused ({t.pause_reason})"
            return

        if result.code in _TERMINAL_CODES:
            t.state = DONE
            t.pause_reason = ""
        elif t.attempts >= self.max_attempts:
            t.state = FAILED
            t.pause_reason = ""
        else:
            self._requeue(result.task_id, f"exit code {result.code}")

    def on_node_lost(self, node_id: str) -> None:
        """A node's connection dropped. Its task returns to the queue so another
        desktop can pick it up -- the point of a fleet is that one dead VM
        doesn't take the work with it."""
        node = self.nodes.pop(node_id, None)
        if node is not None and node.task_id is not None:
            self._requeue(node.task_id, "node lost")

    def pump(self) -> int:
        """Hand queued tasks to idle nodes, up to the concurrency cap. Returns
        how many were dispatched. Call after submitting or on any node event.

        `_order` is already priority-sorted (high first).
        """
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
            t.pause_reason = ""
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

    @property
    def running_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t.state == RUNNING)

    @property
    def queued_count(self) -> int:
        return len(self._order)

    @property
    def paused_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t.state == PAUSED)

    def idle_nodes(self) -> list[Node]:
        return [n for n in self.nodes.values() if not n.busy]

    def is_settled(self) -> bool:
        """True when nothing is running or queued (paused tasks do not block
        settled — they are intentionally on hold)."""
        return self.running_count == 0 and self.queued_count == 0

    def _enqueue(self, task_id: str) -> None:
        """Insert into `_order` by descending priority; stable for equal rank."""
        if task_id in self._order:
            return
        t = self.tasks[task_id]
        insert_at = len(self._order)
        for i, existing_id in enumerate(self._order):
            if self.tasks[existing_id].priority < t.priority:
                insert_at = i
                break
        self._order.insert(insert_at, task_id)

    def _idle_node(self, task: Task) -> Node | None:
        """Prefer a node that has not already failed this task."""
        idle = [n for n in self.nodes.values() if not n.busy]
        if not idle:
            return None
        fresh = [n for n in idle if n.node_id not in task.tried_nodes]
        return (fresh or idle)[0]

    def _requeue(self, task_id: str, reason: str) -> None:
        t = self.tasks.get(task_id)
        if t is None or t.state in (DONE, FAILED, PAUSED):
            return
        if t.attempts >= self.max_attempts:
            t.state = FAILED
            t.node_id = None
            t.pause_reason = ""
            t.detail = reason
            return
        t.state = QUEUED
        t.node_id = None
        t.detail = reason
        t.pause_reason = ""
        self._enqueue(task_id)
