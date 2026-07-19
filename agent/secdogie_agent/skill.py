"""Programmable skills -- the high-level layer above raw macro record/replay.

A recorded macro (macro.py) is a flat list of clicks/types replayed verbatim: no
parameters, no reuse, no branching, and one drifted step drops the whole run to
the live model. A *skill* is instead a small program: named, parameterized flows
that can call each other, branch on a condition, and loop.

    {"skills": {
      "login": {
        "params": ["user", "pass"],
        "body": [
          {"op": "action", "action": "left_click", "x": 500, "y": 300},
          {"op": "action", "action": "type", "text": "{user}"},
          {"op": "action", "action": "key", "keys": ["tab"]},
          {"op": "action", "action": "type", "text": "{pass}"},
          {"op": "if",
           "cond": {"kind": "screen", "description": "a 'remember me' checkbox is visible"},
           "then": [{"op": "action", "action": "left_click", "x": 480, "y": 340}]},
          {"op": "action", "action": "key", "keys": ["Return"]}
        ]
      },
      "process_all": {
        "body": [
          {"op": "while",
           "cond": {"kind": "screen", "description": "there is another unread row"},
           "body": [{"op": "call", "skill": "handle_row"}]}
        ]
      }
    }}

The interpreter here is pure: it walks the program, substitutes `{var}`
placeholders, evaluates conditions, and drives control flow, calling two
injected callbacks -- `execute(action_dict)` to perform a UI action and
`check(description) -> bool` to evaluate a screen condition. That keeps the whole
control-flow layer (calls, branches, loops, recursion/loop guards, parameter
binding) unit-testable with fakes; the real `execute`/`check` (backend + a model
yes/no) are wired in skill_runner.py.

Statements: action / call / if / while / repeat / set / incr.
Conditions:  screen (delegated to `check`) / expr (pure compare) / not.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
_MAX_DEPTH = 32  # call-stack guard against runaway recursion


class SkillError(ValueError):
    """A malformed skill program (unknown op/skill/variable, bad shape)."""


@dataclass(frozen=True)
class SkillResult:
    outcome: str  # "completed" | "stopped" | "limit"
    actions: int  # UI actions executed
    statements: int  # statements walked (compared against the guard)


def _subst(value, env: dict):
    """Replace `{name}` placeholders from `env`, recursing into dict/list values.
    An unbound placeholder is an error -- better to fail loudly than to click on
    a literal "{user}"."""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            name = m.group(1)
            if name not in env:
                raise SkillError(f"unbound variable {{{name}}}")
            return str(env[name])
        return _PLACEHOLDER.sub(repl, value)
    if isinstance(value, dict):
        return {k: _subst(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, env) for v in value]
    return value


def _as_number(v):
    """Best-effort numeric coercion so `expr` conditions compare 3 < 10 as
    numbers, but "abc" == "abc" as strings."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _compare(left, right, op: str) -> bool:
    ln, rn = _as_number(left), _as_number(right)
    if ln is not None and rn is not None:
        left, right = ln, rn
    else:
        left, right = str(left), str(right)
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    raise SkillError(f"unknown comparison operator {op!r}")


def run_skill(
    library: dict,
    entry: str,
    args: dict | None,
    execute: Callable[[dict], object],
    check: Callable[[str], bool],
    *,
    max_statements: int = 2000,
    max_loop: int = 1000,
    should_stop: Callable[[], bool] | None = None,
) -> SkillResult:
    """Run skill `entry` from `library` with `args` bound as its parameters.

    `execute(action_dict)` performs one UI action; `check(description)` answers a
    screen condition. `max_statements` (total) and `max_loop` (per loop) bound
    execution so a bad `while`/recursion can't run forever. Returns how it ended
    plus counts."""
    skills = library.get("skills", library) if isinstance(library, dict) else {}
    if entry not in skills:
        raise SkillError(f"unknown entry skill {entry!r}")

    state = {"stmt": 0, "actions": 0, "reason": ""}  # "reason": "" | "stopped" | "limit"

    def eval_cond(cond: dict, env: dict) -> bool:
        kind = cond.get("kind")
        if kind == "screen":
            return bool(check(_subst(cond["description"], env)))
        if kind == "expr":
            return _compare(_subst(cond["left"], env), _subst(cond["right"], env), cond.get("op", "=="))
        if kind == "not":
            return not eval_cond(cond["cond"], env)
        raise SkillError(f"unknown condition kind {kind!r}")

    def run_body(body: list, env: dict, depth: int) -> None:
        for stmt in body:
            if state["reason"]:
                return
            if should_stop is not None and should_stop():
                state["reason"] = "stopped"
                return
            if state["stmt"] >= max_statements:
                state["reason"] = "limit"
                return
            state["stmt"] += 1

            op = stmt.get("op")
            if op == "action":
                execute(_subst({k: v for k, v in stmt.items() if k != "op"}, env))
                state["actions"] += 1
            elif op == "set":
                env[stmt["var"]] = _subst(stmt["value"], env)
            elif op == "incr":
                cur = _as_number(env.get(stmt["var"], 0)) or 0.0
                by = _as_number(stmt.get("by", 1)) or 0.0
                nxt = cur + by
                env[stmt["var"]] = int(nxt) if nxt == int(nxt) else nxt
            elif op == "call":
                if depth >= _MAX_DEPTH:
                    raise SkillError("skill call depth exceeded (recursion runaway?)")
                name = stmt["skill"]
                if name not in skills:
                    raise SkillError(f"call to unknown skill {name!r}")
                call_args = {k: _subst(v, env) for k, v in stmt.get("args", {}).items()}
                run_body(skills[name].get("body", []), dict(call_args), depth + 1)
            elif op == "if":
                branch = "then" if eval_cond(stmt["cond"], env) else "else"
                run_body(stmt.get(branch, []), env, depth)
            elif op == "repeat":
                raw = stmt["count"]
                count = int(float(_subst(raw, env))) if isinstance(raw, str) else int(raw)
                for i in range(max(0, min(count, max_loop))):
                    if stmt.get("var"):
                        env[stmt["var"]] = i + 1  # 1-based iteration index
                    run_body(stmt.get("body", []), env, depth)
                    if state["reason"]:
                        return
            elif op == "while":
                iters = 0
                while eval_cond(stmt["cond"], env):
                    if iters >= max_loop:
                        state["reason"] = "limit"
                        return
                    iters += 1
                    run_body(stmt.get("body", []), env, depth)
                    if state["reason"]:
                        return
            else:
                raise SkillError(f"unknown statement op {op!r}")

    run_body(skills[entry].get("body", []), dict(args or {}), 0)
    outcome = state["reason"] or "completed"
    return SkillResult(outcome=outcome, actions=state["actions"], statements=state["stmt"])
