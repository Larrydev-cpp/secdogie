"""Task decomposition + progress tracking -- the agent's little planning layer.

A single long task ("export this video and upload it") makes the model
re-derive, every step, where it is in a multi-part job it can't see the shape of.
So when planning is on, the model first breaks the task into a short ordered
list of concrete sub-tasks, and the loop then works ONE sub-task at a time,
carrying the plan + progress forward in the prompt. That gives the model a
stable sense of place (state management) instead of reconstructing it from the
last 10 actions each turn, and it gives the loop a unit to recover at: if a
sub-task burns through its step budget without finishing, the loop skips it and
moves on rather than spinning the whole run to max_steps.

`Plan` is pure state -- no model calls, no I/O -- so the progression logic is
fully testable on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Plan:
    """An ordered list of sub-tasks and a cursor over them. `completed` and
    `skipped` record how each finished, so the end-of-run summary and exit code
    can tell "finished cleanly" from "gave up on part of it"."""

    subtasks: list[str]
    index: int = 0
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def current(self) -> str | None:
        return self.subtasks[self.index] if 0 <= self.index < len(self.subtasks) else None

    @property
    def is_done(self) -> bool:
        return self.index >= len(self.subtasks)

    def complete_current(self) -> None:
        """Mark the current sub-task done and advance."""
        if self.current is not None:
            self.completed.append(self.current)
            self.index += 1

    def skip_current(self) -> None:
        """Give up on the current sub-task (stuck / budget exceeded) and advance."""
        if self.current is not None:
            self.skipped.append(self.current)
            self.index += 1

    def progress_note(self) -> str:
        """The block injected into the prompt each step: the whole plan, what's
        done, and -- explicitly -- that `done` means "this sub-task is finished",
        not the whole task. Without that last point the model would end the run
        at the first sub-task."""
        lines = []
        for i, sub in enumerate(self.subtasks):
            if i < self.index:
                mark = "[x]" if sub in self.completed else "[skipped]"
                lines.append(f"  {mark} {sub}")
            elif i == self.index:
                lines.append(f"  -> [ ] {sub}   <-- CURRENT sub-task")
            else:
                lines.append(f"     [ ] {sub}")
        body = "\n".join(lines)
        return (
            f"PLAN PROGRESS ({len(self.completed)}/{len(self.subtasks)} done). Work ONLY on the "
            'CURRENT sub-task. Reply {"action":"done"} when THIS sub-task is complete (not the whole '
            f"task) and the loop will move you to the next one.\n{body}"
        )
