"""On-machine wiring for programmable skills: load a skill library and run an
entry skill against a real target -- backend.execute for actions, and a model
yes/no for `screen` conditions.

The interpreter (skill.py) is the tested core; this is the thin adapter that
gives it hands (the backend) and eyes (the provider). Skill coordinates are
authored in real target pixels, so unlike the model loop there is no
scale/translate step -- the action dict goes almost straight to the backend.
"""
from __future__ import annotations

import json

from . import safety, screen
from .backend import Backend, DesktopBackend
from .providers.base import Action, VisionProvider
from .skill import run_skill

# Action fields that must be numbers even after `{var}` substitution turns them
# into strings, so a parameterized coordinate/count still reaches pyautogui as
# the right type.
_INT_FIELDS = ("x", "y", "to_x", "to_y", "dx", "dy")


def load_library(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _to_action(action_dict: dict) -> Action:
    d = dict(action_dict)
    for k in _INT_FIELDS:
        if isinstance(d.get(k), str):
            d[k] = int(float(d[k]))
    if isinstance(d.get("seconds"), str):
        d["seconds"] = float(d["seconds"])
    return Action.from_dict(d)


def run_skill_file(
    provider: VisionProvider,
    skill_path: str,
    entry: str,
    args: dict | None = None,
    *,
    backend: Backend | None = None,
    region: tuple[int, int, int, int] | None = None,
    max_image_edge: int = screen.DEFAULT_MAX_EDGE,
    auto: bool = False,
    logger_name: str = "secdogie_skill",
) -> int:
    """Run skill `entry` from the library at `skill_path`. Returns a process exit
    code: 0 completed, 1 stopped/hit a guard, 2 a bad skill program."""
    from .skill import SkillError

    logger = safety.setup_logging(None, name=logger_name)
    backend = backend or DesktopBackend()
    backend.setup(logger)

    try:
        library = load_library(skill_path)
    except (OSError, ValueError) as e:
        logger.error("could not load skill file %s: %s", skill_path, e)
        return 2

    def execute(action_dict: dict) -> str:
        action = _to_action(action_dict)
        if not auto and not safety.confirm(f"Execute {action.kind}({action.raw})?"):
            logger.info("user declined action: %s", action.kind)
            return "skipped (user declined)"
        result = backend.execute(action)
        logger.info("action: %s -> %s", action.kind, result)
        return result

    def check(description: str) -> bool:
        raw_png, real_size = backend.capture(region)
        model_png, model_size, _scale = screen.prepare_for_model(raw_png, real_size, max_edge=max_image_edge)
        answer = provider.check_condition(description, model_png, model_size)
        logger.info("condition %r -> %s", description, "yes" if answer else "no")
        return answer

    try:
        result = run_skill(library, entry, args or {}, execute, check)
    except SkillError as e:
        logger.error("skill error: %s", e)
        return 2

    logger.info(
        "skill %r finished: %s (%d action(s), %d statement(s))",
        entry, result.outcome, result.actions, result.statements,
    )
    return 0 if result.outcome == "completed" else 1
