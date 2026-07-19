"""Provider-agnostic contract between the agent loop and a vision-capable LLM.

Providers only need to turn (task, screenshot, history) into one Action.
Everything else -- taking screenshots, executing actions, confirmations,
step limits -- lives in the agent loop, not the provider.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any

# The full set of actions the agent loop knows how to execute. A provider's
# job is to emit a dict that validates against this shape; see
# Action.from_dict for the exact fields expected per action kind.
VALID_ACTIONS = {
    "left_click",
    "right_click",
    "double_click",
    "move",
    "drag",
    "type",
    "key",
    "hold_key",
    "scroll",
    "open",
    "wait",
    "screenshot",
    "track_click",
    "click_element",
    "remember",
    "done",
    "ask_user",
}


@dataclass
class Action:
    kind: str
    x: int | None = None
    y: int | None = None
    to_x: int | None = None
    to_y: int | None = None
    text: str | None = None
    keys: list[str] | None = None
    dx: int | None = None
    dy: int | None = None
    seconds: float | None = None
    path: str | None = None
    element: str | None = None  # accessibility-tree target ref for click_element (e.g. "e3")
    reasoning: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Action:
        kind = d.get("action")
        if kind not in VALID_ACTIONS:
            raise ValueError(f"unknown action {kind!r}; must be one of {sorted(VALID_ACTIONS)}")
        return Action(
            kind=kind,
            x=d.get("x"),
            y=d.get("y"),
            to_x=d.get("to_x"),
            to_y=d.get("to_y"),
            text=d.get("text"),
            keys=d.get("keys"),
            dx=d.get("dx"),
            dy=d.get("dy"),
            seconds=d.get("seconds"),
            path=d.get("path"),
            element=(str(d["element"]) if d.get("element") is not None else None),
            reasoning=d.get("reasoning"),
            raw=d,
        )

    def scaled(self, factor: float) -> Action:
        """Return a copy with all pixel coordinates multiplied by `factor`.

        The model reasons in the (possibly downscaled) image's coordinate
        space; this maps its coordinates back to real screen pixels. Only true
        pixel positions (x/y/to_x/to_y) are scaled -- scroll amounts (dx/dy)
        are not screen coordinates and are left alone. `raw` is updated too so
        logs and confirmation prompts show the coordinates that will actually
        be clicked, not the model-space ones.
        """
        if factor == 1.0:
            return self

        def s(v: int | None) -> int | None:
            return int(round(v * factor)) if v is not None else None

        new_raw = dict(self.raw)
        for k in ("x", "y", "to_x", "to_y"):
            if isinstance(new_raw.get(k), (int, float)):
                new_raw[k] = int(round(new_raw[k] * factor))

        return replace(
            self,
            x=s(self.x),
            y=s(self.y),
            to_x=s(self.to_x),
            to_y=s(self.to_y),
            raw=new_raw,
        )

    def translated(self, dx: int, dy: int) -> Action:
        """Return a copy with all pixel coordinates shifted by (dx, dy).

        Used when the screenshot the model reasoned about was cropped to a
        sub-region of the real screen (e.g. one window out of several being
        driven in parallel): model coordinates are region-relative, so this
        adds the region's (left, top) origin back to get real, absolute
        screen coordinates pyautogui can act on. Like `scaled`, scroll
        amounts (dx/dy fields) are not positions and are left alone.
        """
        if dx == 0 and dy == 0:
            return self

        def t(v: int | None, delta: int) -> int | None:
            return v + delta if v is not None else None

        new_raw = dict(self.raw)
        for k, delta in (("x", dx), ("y", dy), ("to_x", dx), ("to_y", dy)):
            if isinstance(new_raw.get(k), (int, float)):
                new_raw[k] = new_raw[k] + delta

        return replace(
            self,
            x=t(self.x, dx),
            y=t(self.y, dy),
            to_x=t(self.to_x, dx),
            to_y=t(self.to_y, dy),
            raw=new_raw,
        )


@dataclass
class HistoryStep:
    action: Action
    result: str  # short human-readable summary of what happened, fed back to the model


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_action_json(text: str) -> dict[str, Any]:
    """Extracts and parses the first JSON object found in `text`.

    Models routinely wrap JSON in markdown code fences or add a sentence of
    preamble despite instructions not to; this tolerates both rather than
    demanding perfectly bare JSON.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise ValueError(f"no JSON object found in model output: {text!r}")
    return json.loads(match.group(0))


_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def parse_plan(text: str) -> list[str]:
    """Parse a model's task decomposition into an ordered list of sub-task
    strings. Prefers a JSON array; falls back to numbered/bulleted lines so a
    model that ignores the "JSON only" instruction still yields a usable plan.
    Returns [] when nothing list-like is found (the caller then runs unplanned).
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()

    match = _JSON_ARRAY_RE.search(stripped)
    if match:
        try:
            arr = json.loads(match.group(0))
            items = [str(x).strip() for x in arr if str(x).strip()]
            if items:
                return items
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: one sub-task per non-empty line, stripping list markers.
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*0123456789.)( ").strip()
        if line:
            out.append(line)
    return out


class VisionProvider:
    """Base class for a vision-LLM backed action source."""

    def next_action(
        self,
        task: str,
        screenshot_png: bytes,
        screen_size: tuple[int, int],
        history: list[HistoryStep],
    ) -> Action:
        raise NotImplementedError

    def explain_task(
        self,
        task: str,
        screenshot_png: bytes,
        screen_size: tuple[int, int],
    ) -> str | None:
        """Return a short, plain-language restatement of the task plus a rough
        plan, shown to the user for confirmation before the agent acts. A
        provider that doesn't support briefings returns None (the agent then
        skips the briefing step)."""
        return None

    def plan_task(
        self,
        task: str,
        screenshot_png: bytes,
        screen_size: tuple[int, int],
    ) -> list[str] | None:
        """Decompose the task into an ordered list of concrete sub-tasks, used
        when the loop runs with planning on (see plan.py). Return None if the
        provider doesn't support planning (the loop then runs unplanned); an
        empty/short list is fine and just means "no useful split"."""
        return None

    def check_condition(
        self,
        question: str,
        screenshot_png: bytes,
        screen_size: tuple[int, int],
    ) -> bool:
        """Answer a yes/no question about the current screen, used to evaluate a
        skill's `screen` condition (see skill.py / skill_runner.py). Providers
        that can't do this raise NotImplementedError."""
        raise NotImplementedError("this provider does not support screen condition checks")
