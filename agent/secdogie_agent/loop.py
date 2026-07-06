"""The core agent loop: screenshot -> model picks one action -> confirm ->
execute -> feed the result back -> repeat, until the model says `done`, the
user stops it, or `max_steps` is hit.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import actions, safety, screen
from .providers.base import HistoryStep, VisionProvider


@dataclass
class AgentConfig:
    task: str
    max_steps: int = 50
    auto: bool = False  # if False (default), every action needs a y/N confirmation
    dry_run: bool = False  # still calls the model each step, but never touches mouse/keyboard
    log_path: str | None = None


def run(provider: VisionProvider, config: AgentConfig) -> int:
    """Returns a process-style exit code: 0 done, 1 provider error,
    2 user declined to continue past an ask_user, 3 max_steps exhausted,
    4 no graphical display available to screenshot."""
    logger = safety.setup_logging(config.log_path)
    logger.info("task: %s", config.task)
    if config.auto:
        logger.warning("running with --auto: actions execute without per-step confirmation")
    if config.dry_run:
        logger.info("running with --dry-run: actions will be logged but not executed")

    try:
        import pyautogui

        pyautogui.FAILSAFE = True  # slamming the cursor into a screen corner aborts pyautogui calls
    except Exception as e:
        # Not just ImportError: pyautogui's own import chain (mouseinfo) raises other
        # exceptions (e.g. KeyError on DISPLAY) when there's no GUI session at all.
        logger.warning("pyautogui unavailable (%s); only --dry-run will work", e)

    history: list[HistoryStep] = []

    for step in range(1, config.max_steps + 1):
        try:
            png, size = screen.capture_screenshot()
        except screen.NoDisplayError as e:
            logger.error("%s", e)
            return 4
        try:
            action = provider.next_action(config.task, png, size, history)
        except Exception as e:
            logger.error("provider failed to produce an action: %s", e)
            return 1

        reasoning = action.reasoning or action.raw.get("reasoning", "")
        logger.info("step %d/%d: %s %s", step, config.max_steps, action.kind, f"({reasoning})" if reasoning else "")

        if action.kind == "done":
            summary = action.text or action.raw.get("text", "")
            logger.info("done: %s", summary)
            return 0

        if action.kind == "ask_user":
            question = action.text or action.raw.get("text", "")
            print(f"\n[secdogie-agent] the model is asking: {question}")
            if not safety.confirm("Allow the agent to continue?"):
                logger.info("user declined to continue after ask_user")
                return 2
            history.append(HistoryStep(action=action, result="user confirmed, continuing"))
            continue

        if config.dry_run:
            logger.info("[dry-run] would execute: %s", action.raw)
            history.append(HistoryStep(action=action, result="skipped (dry-run)"))
            continue

        if not config.auto and not safety.confirm(f"Execute {action.kind}({action.raw})?"):
            logger.info("user declined action: %s", action.kind)
            history.append(HistoryStep(action=action, result="skipped (user declined)"))
            continue

        try:
            result = actions.execute(action)
        except Exception as e:
            result = f"error: {e}"
            logger.error("action failed: %s", e)
        history.append(HistoryStep(action=action, result=result))

    logger.warning("reached max_steps (%d) without the model signaling done", config.max_steps)
    return 3
