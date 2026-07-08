"""The core agent loop: screenshot -> model picks one action -> confirm ->
execute -> feed the result back -> repeat, until the model says `done`, the
user stops it, or `max_steps` is hit.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from . import actions, dialog, safety, screen
from .providers.base import HistoryStep, VisionProvider

# Benign actions that never need a confirmation prompt -- they don't touch the
# mouse/keyboard in a way that can do harm.
_BENIGN = {"wait", "screenshot"}

# Injected into the task when --watch is on, turning the loop into a monitor:
# most frames the model just reports "keep watching"; it only acts on a trigger.
_WATCH_DIRECTIVE = """\

MONITORING MODE: You are watching the screen continuously, frame by frame.
On each frame, decide whether the situation described in the task has occurred:
- If it has NOT occurred yet, reply with {"action": "wait", "reasoning": "..."} \
and nothing else -- do not act.
- Only when it HAS occurred, perform the appropriate action (e.g. "open" a file, \
click, type).
Use "done" only if the task is a one-shot that is now fully complete and no more \
watching is needed."""


@dataclass
class AgentConfig:
    task: str
    max_steps: int = 50
    auto: bool = False  # if False (default), every action needs a y/N confirmation
    dry_run: bool = False  # still calls the model each step, but never touches mouse/keyboard
    log_path: str | None = None
    max_image_edge: int = screen.DEFAULT_MAX_EDGE  # long-edge cap for the image sent to the model
    grid: bool = False  # overlay a labeled coordinate grid to help the model aim
    move_duration: float = actions.DEFAULT_MOVE_DURATION  # cursor glide time (seconds)
    settle: float = actions.DEFAULT_SETTLE  # hover pause before a click (seconds)
    gui: bool = False  # use tkinter dialogs for the task briefing and ask_user prompts
    watch: bool = False  # monitor mode: poll frames, act only when a condition triggers
    watch_interval: float = 2.0  # minimum seconds between frames in watch mode
    region: tuple[int, int, int, int] | None = None  # (left, top, width, height); None = full primary monitor
    logger_name: str = "secdogie_agent"  # distinct per concurrent run so loggers don't share/race on handlers
    should_stop: Callable[[], bool] | None = None  # checked each step; lets a caller cancel a running loop


def run(provider: VisionProvider, config: AgentConfig) -> int:
    """Returns a process-style exit code: 0 done, 1 provider error,
    2 user declined to continue past an ask_user, 3 max_steps exhausted,
    4 no graphical display available to screenshot, 5 stopped via should_stop."""
    logger = safety.setup_logging(config.log_path, name=config.logger_name)
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

    if config.gui:
        briefing_rc = _run_briefing(provider, config, logger)
        if briefing_rc is not None:
            return briefing_rc

    effective_task = config.task + _WATCH_DIRECTIVE if config.watch else config.task
    if config.watch:
        logger.info("watch mode: polling every %.1fs until the trigger condition occurs", config.watch_interval)

    history: list[HistoryStep] = []

    for step in range(1, config.max_steps + 1):
        if config.should_stop is not None and config.should_stop():
            logger.info("stopped externally after %d step(s)", step - 1)
            return 5

        # In watch mode, pace the polling so we don't hammer the API.
        if config.watch and step > 1:
            time.sleep(config.watch_interval)

        try:
            raw_png, real_size = screen.capture_screenshot(region=config.region)
        except screen.NoDisplayError as e:
            logger.error("%s", e)
            return 4
        # Downscale to a known size and remember the factor to map the model's
        # coordinates back to real screen pixels -- this is what keeps clicks
        # landing on target.
        model_png, model_size, scale = screen.prepare_for_model(
            raw_png, real_size, max_edge=config.max_image_edge, grid=config.grid
        )
        try:
            action = provider.next_action(effective_task, model_png, model_size, history)
        except Exception as e:
            logger.error("provider failed to produce an action: %s", e)
            return 1
        action = action.scaled(scale)
        if config.region is not None:
            # Model coordinates are relative to the captured region; shift
            # back to absolute screen coordinates before anything downstream
            # (confirmation prompts, execution) sees them.
            action = action.translated(config.region[0], config.region[1])

        reasoning = action.reasoning or action.raw.get("reasoning", "")

        # In watch mode a "wait" means "trigger not seen yet" -- log quietly and
        # keep watching without a confirmation prompt.
        if config.watch and action.kind == "wait":
            logger.info("watching (step %d): no trigger yet%s", step, f" -- {reasoning}" if reasoning else "")
            history.append(HistoryStep(action=action, result="watching, condition not met"))
            continue

        logger.info("step %d/%d: %s %s", step, config.max_steps, action.kind, f"({reasoning})" if reasoning else "")

        if action.kind == "done":
            summary = action.text or action.raw.get("text", "")
            logger.info("done: %s", summary)
            return 0

        if action.kind == "ask_user":
            question = action.text or action.raw.get("text", "")
            logger.info("model is asking: %s", question)
            if config.gui:
                allowed = dialog.ask_user(question)
            else:
                print(f"\n[secdogie-agent] the model is asking: {question}")
                allowed = safety.confirm("Allow the agent to continue?")
            if not allowed:
                logger.info("user declined to continue after ask_user")
                return 2
            history.append(HistoryStep(action=action, result="user confirmed, continuing"))
            continue

        if config.dry_run:
            logger.info("[dry-run] would execute: %s", action.raw)
            history.append(HistoryStep(action=action, result="skipped (dry-run)"))
            continue

        needs_confirm = not config.auto and action.kind not in _BENIGN
        if needs_confirm and not safety.confirm(f"Execute {action.kind}({action.raw})?"):
            logger.info("user declined action: %s", action.kind)
            history.append(HistoryStep(action=action, result="skipped (user declined)"))
            continue

        try:
            result = actions.execute(
                action, move_duration=config.move_duration, settle=config.settle
            )
        except Exception as e:
            result = f"error: {e}"
            logger.error("action failed: %s", e)
        history.append(HistoryStep(action=action, result=result))

    logger.warning("reached max_steps (%d) without the model signaling done", config.max_steps)
    return 3


def _run_briefing(provider: VisionProvider, config: AgentConfig, logger) -> int | None:
    """Before acting, have the model restate the task and its plan, and show it
    in a GUI dialog for approval. Returns None to proceed, or an exit code to
    stop (2 = user cancelled, 4 = no display)."""
    try:
        raw_png, real_size = screen.capture_screenshot(region=config.region)
    except screen.NoDisplayError as e:
        logger.error("%s", e)
        return 4

    model_png, _size, _scale = screen.prepare_for_model(
        raw_png, real_size, max_edge=config.max_image_edge
    )
    try:
        plan = provider.explain_task(config.task, model_png, real_size)
    except Exception as e:
        # A briefing failure shouldn't block the run; just note it and continue.
        logger.warning("could not get a task briefing from the model: %s", e)
        return None

    if not plan:
        return None  # provider doesn't do briefings

    logger.info("task briefing:\n%s", plan)
    if not dialog.confirm_plan(config.task, plan):
        logger.info("user cancelled at the task briefing")
        return 2
    return None
