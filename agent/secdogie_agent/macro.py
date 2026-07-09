"""Record/replay macros -- the actual "robotic" half of RPA.

The live agent loop (screenshot -> model picks an action -> execute) is the
"figure out the process" half; once a task has been driven successfully,
its action sequence can be saved as a macro and replayed later with *zero*
model calls: fast, free, deterministic. A step is recorded against a
`backend.ElementSelector` (via the backend's optional Locatable capability)
when the backend can identify what was clicked, so replay re-finds the
target even if the screen has shifted slightly; backends that can't identify
elements (see backend.Locatable) fall back to a resolution-independent
normalized coordinate for that step instead.

Replay is deliberately all-or-nothing per step: resolve_replay_step returns
None the moment a step can't be resolved (selector no longer matches, i.e.
the UI changed), and the caller (loop.run) falls back to the live model loop
from that point on, rather than guessing. See loop.py for how the two are
stitched together.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backend import Backend, ElementSelector, Locatable
from .providers.base import Action

MACRO_FORMAT_VERSION = 1

# Action fields a macro step carries verbatim (replayed as-is, unlike x/y/
# to_x/to_y which get re-resolved via a selector or normalized coordinate).
_NON_POSITIONAL_FIELDS = ("text", "keys", "dx", "dy", "seconds", "path")


class MacroReplayError(RuntimeError):
    """A recorded step could not be replayed -- its selector no longer
    matches anything (the UI changed), or no fallback coordinate exists.
    Carries the step index so the caller can log exactly where replay broke
    down and decide whether/how to continue."""

    def __init__(self, step_index: int, reason: str):
        super().__init__(f"macro replay failed at step {step_index}: {reason}")
        self.step_index = step_index
        self.reason = reason


@dataclass(frozen=True)
class MacroStep:
    kind: str
    fields: dict[str, Any] = field(default_factory=dict)  # text/keys/dx/dy/seconds/path, whichever apply
    selector: ElementSelector | None = None  # re-locate x/y (and to_x/to_y for drag) at replay time
    point: tuple[float, float] | None = None  # normalized (0..1, 0..1) fallback for x/y when no selector
    to_point: tuple[float, float] | None = None  # normalized fallback for to_x/to_y (drag's second endpoint)
    recorded_result: str = ""  # what happened when this step was originally executed, for a human reading the file

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "fields": self.fields,
            "selector": {"kind": self.selector.kind, "attrs": self.selector.attrs} if self.selector else None,
            "point": list(self.point) if self.point else None,
            "to_point": list(self.to_point) if self.to_point else None,
            "recorded_result": self.recorded_result,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> "MacroStep":
        sel = d.get("selector")
        return MacroStep(
            kind=d["kind"],
            fields=d.get("fields") or {},
            selector=ElementSelector(kind=sel["kind"], attrs=sel["attrs"]) if sel else None,
            point=tuple(d["point"]) if d.get("point") else None,
            to_point=tuple(d["to_point"]) if d.get("to_point") else None,
            recorded_result=d.get("recorded_result", ""),
        )


@dataclass
class Macro:
    task: str
    steps: list[MacroStep] = field(default_factory=list)
    created_at: float = 0.0

    def save(self, path: str | Path) -> None:
        payload = {
            "format_version": MACRO_FORMAT_VERSION,
            "task": self.task,
            "created_at": self.created_at,
            "steps": [s.to_json() for s in self.steps],
        }
        Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Macro":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        version = data.get("format_version")
        if version != MACRO_FORMAT_VERSION:
            raise ValueError(
                f"{path}: unsupported macro format_version {version!r} (expected {MACRO_FORMAT_VERSION})"
            )
        return cls(
            task=data["task"],
            steps=[MacroStep.from_json(s) for s in data["steps"]],
            created_at=data.get("created_at", 0.0),
        )


def _normalize(x: int, y: int, screen_size: tuple[int, int]) -> tuple[float, float]:
    w, h = screen_size
    return (x / w if w else 0.0, y / h if h else 0.0)


def _denormalize(point: tuple[float, float], screen_size: tuple[int, int]) -> tuple[int, int]:
    w, h = screen_size
    return (round(point[0] * w), round(point[1] * h))


class MacroRecorder:
    """Accumulates steps during a live run. Call `record` right after an
    action actually executes (not for dry-run/declined/watch-idle steps --
    a macro should reflect a real, successful sequence)."""

    def __init__(self, task: str):
        self.task = task
        self.steps: list[MacroStep] = []

    def record(self, action: Action, result: str, backend: Backend, screen_size: tuple[int, int]) -> None:
        fields = {f: getattr(action, f) for f in _NON_POSITIONAL_FIELDS if getattr(action, f) is not None}

        selector: ElementSelector | None = None
        point: tuple[float, float] | None = None
        if action.x is not None and action.y is not None:
            if isinstance(backend, Locatable):
                selector = backend.describe_target(action.x, action.y)
            if selector is None:
                point = _normalize(action.x, action.y, screen_size)

        to_point: tuple[float, float] | None = None
        if action.to_x is not None and action.to_y is not None:
            # Drag's second endpoint only ever gets a normalized fallback --
            # one selector can't stand for both "from" and "to" at once, and
            # a single extra selector lookup per drag isn't worth the
            # complexity dragging onto a specific element is rare enough.
            to_point = _normalize(action.to_x, action.to_y, screen_size)

        self.steps.append(
            MacroStep(kind=action.kind, fields=fields, selector=selector, point=point, to_point=to_point,
                      recorded_result=result)
        )

    def record_step(self, step: MacroStep) -> None:
        """Append an already-built step verbatim -- used when a step came
        from a successful replay rather than a fresh live execution, so
        resaving the macro after a run doesn't need to re-derive a selector
        for a step that's already proven to work."""
        self.steps.append(step)

    def build(self) -> Macro:
        return Macro(task=self.task, steps=list(self.steps), created_at=time.time())


def resolve_replay_step(step: MacroStep, backend: Backend, screen_size: tuple[int, int]) -> Action | None:
    """Turns a recorded step back into an Action ready for the loop's normal
    execute/confirm/log pipeline, resolving its position fresh against the
    current screen. Returns None if the step can't be resolved (selector no
    longer matches, or a selector-only step hit a non-Locatable backend)."""
    x = y = to_x = to_y = None

    if step.selector is not None:
        if not isinstance(backend, Locatable):
            return None  # this backend can't resolve any selector at all
        found = backend.locate(step.selector)
        if found is None:
            return None  # the UI changed -- the element isn't there anymore
        x, y = found
    elif step.point is not None:
        x, y = _denormalize(step.point, screen_size)

    if step.to_point is not None:
        to_x, to_y = _denormalize(step.to_point, screen_size)

    raw: dict[str, Any] = {"action": step.kind}
    if x is not None:
        raw["x"] = x
    if y is not None:
        raw["y"] = y
    if to_x is not None:
        raw["to_x"] = to_x
    if to_y is not None:
        raw["to_y"] = to_y
    raw.update(step.fields)

    return Action(kind=step.kind, x=x, y=y, to_x=to_x, to_y=to_y, raw=raw, **step.fields)
