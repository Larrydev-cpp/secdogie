"""The core agent loop: screenshot -> model picks one action -> confirm ->
execute -> feed the result back -> repeat, until the model says `done`, the
user stops it, or `max_steps` is hit.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import actions, dialog, safety, screen
from .backend import Backend, DesktopBackend
from .macro import Macro, MacroRecorder, MacroStep, resolve_replay_step
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
    # After executing a real action, wait this long before the next screenshot so the
    # UI can react -- otherwise a fast model outruns a slow-animating app and acts on a
    # stale frame ("last action hasn't landed, next screenshot already sent"). 0 disables.
    action_pause: float = 0.4
    # If the model picks the same action against an unchanged screen this many times in a
    # row, the action isn't landing (dead control / frozen render) -- stop instead of
    # spinning to max_steps. 0 disables. Benign/terminal actions (wait/done/...) are exempt.
    stall_limit: int = 4
    region: tuple[int, int, int, int] | None = None  # (left, top, width, height); None = full primary monitor
    logger_name: str = "secdogie_agent"  # distinct per concurrent run so loggers don't share/race on handlers
    should_stop: Callable[[], bool] | None = None  # checked each step; lets a caller cancel a running loop
    backend: Backend | None = None  # what to drive; None = the local desktop (mss + pyautogui)
    # RPA: if set, replay this macro file (zero model calls) with a live fallback the moment a step
    # can't be resolved; a run that finishes with `done` re-saves the full sequence here. See macro.py.
    macro_path: str | None = None


def run(provider: VisionProvider, config: AgentConfig) -> int:
    """Returns a process-style exit code: 0 done, 1 provider error,
    2 user declined to continue past an ask_user, 3 max_steps exhausted,
    4 no graphical display available to screenshot, 5 stopped via should_stop,
    6 stalled (same action, unchanged screen, stall_limit times)."""
    logger = safety.setup_logging(config.log_path, name=config.logger_name)
    logger.info("task: %s", config.task)
    if config.auto:
        logger.warning("running with --auto: actions execute without per-step confirmation")
    if config.dry_run:
        logger.info("running with --dry-run: actions will be logged but not executed")

    backend = config.backend or DesktopBackend(
        move_duration=config.move_duration, settle=config.settle
    )
    backend.setup(logger)

    if config.gui:
        briefing_rc = _run_briefing(provider, config, logger, backend)
        if briefing_rc is not None:
            return briefing_rc

    effective_task = config.task + _WATCH_DIRECTIVE if config.watch else config.task
    if config.watch:
        logger.info("watch mode: polling every %.1fs until the trigger condition occurs", config.watch_interval)

    # RPA: replay_steps holds the macro while it's still being trusted; a step
    # that can't be resolved (selector not found -- the UI changed) clears it
    # to None, permanently switching the rest of this run to the live model
    # loop below. Watch mode is exempted -- it waits for a variable-length
    # trigger, which doesn't fit a fixed recorded sequence.
    replay_steps: list[MacroStep] | None = None
    replay_index = 0
    macro_recorder: MacroRecorder | None = None
    if config.macro_path and not config.watch:
        macro_recorder = MacroRecorder(config.task)
        try:
            replay_steps = Macro.load(config.macro_path).steps
            logger.info(
                "replaying macro %s (%d step(s)); falling back to the live model if a step can't be resolved",
                config.macro_path, len(replay_steps),
            )
        except FileNotFoundError:
            pass  # no macro yet -- this run's own successful steps will create one
        except (OSError, ValueError) as e:
            logger.warning("could not load macro %s (%s); running live instead", config.macro_path, e)

    history: list[HistoryStep] = []
    # Stall detection: the signature + captured-frame hash of the last executed
    # action, and how many times in a row the same action met an unchanged screen.
    prev_exec_sig: tuple | None = None
    prev_exec_frame: bytes | None = None
    stall_count = 0

    for step in range(1, config.max_steps + 1):
        if config.should_stop is not None and config.should_stop():
            logger.info("stopped externally after %d step(s)", step - 1)
            return 5

        # In watch mode, pace the polling so we don't hammer the API.
        if config.watch and step > 1:
            time.sleep(config.watch_interval)

        try:
            raw_png, real_size = backend.capture(config.region)
        except screen.CaptureError as e:
            logger.error("%s", e)
            return 4

        frame_hash = hashlib.blake2b(raw_png, digest_size=16).digest()

        # RPA: try the next macro step before ever asking the model -- that's
        # the whole point (fast, free, deterministic). A step that resolves
        # (selector still matches, or a normalized fallback point) is used
        # as-is, already in real screen coordinates. The moment one doesn't
        # resolve, give up on replay for the rest of this run so the live
        # model below can adapt to whatever changed.
        action = None
        from_replay = False
        replayed_step: MacroStep | None = None
        if replay_steps is not None and replay_index < len(replay_steps):
            candidate = replay_steps[replay_index]
            resolved = resolve_replay_step(candidate, backend, real_size)
            if resolved is not None:
                action, from_replay, replayed_step = resolved, True, candidate
                replay_index += 1
            else:
                logger.warning(
                    "macro replay: step %d (%s) could not be resolved (the UI may have changed) -- "
                    "switching to the live model for the rest of this run",
                    replay_index, candidate.kind,
                )
                replay_steps = None

        if action is None:
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
            if macro_recorder is not None and macro_recorder.steps:
                try:
                    macro_recorder.build().save(config.macro_path)
                    logger.info("saved macro: %s (%d step(s))", config.macro_path, len(macro_recorder.steps))
                except OSError as e:
                    logger.warning("could not save macro %s (%s); the run still succeeded", config.macro_path, e)
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

        # Stall guard: the same real action about to run against a screen that
        # hasn't changed since the last one means that last action didn't land.
        if config.stall_limit and action.kind not in _BENIGN:
            sig = (action.kind, action.x, action.y, action.to_x, action.to_y,
                   action.text, tuple(action.keys or ()))
            if sig == prev_exec_sig and frame_hash == prev_exec_frame:
                stall_count += 1
                if stall_count >= config.stall_limit:
                    logger.warning(
                        "stalled: '%s' repeated %d times with no screen change; stopping",
                        action.kind, stall_count,
                    )
                    return 6
            else:
                stall_count = 0
            prev_exec_sig, prev_exec_frame = sig, frame_hash

        needs_confirm = not config.auto and action.kind not in _BENIGN
        if needs_confirm and not safety.confirm(f"Execute {action.kind}({action.raw})?"):
            logger.info("user declined action: %s", action.kind)
            history.append(HistoryStep(action=action, result="skipped (user declined)"))
            continue

        try:
            result = backend.execute(action)
        except Exception as e:
            result = f"error: {e}"
            logger.error("action failed: %s", e)
        else:
            if macro_recorder is not None:
                if from_replay:
                    macro_recorder.record_step(replayed_step)
                else:
                    macro_recorder.record(action, result, backend, real_size)
        history.append(HistoryStep(action=action, result=result))

        # Let the UI react before the next screenshot; benign actions (wait)
        # already pace themselves, so they're exempt.
        if config.action_pause > 0 and action.kind not in _BENIGN:
            time.sleep(config.action_pause)

    logger.warning("reached max_steps (%d) without the model signaling done", config.max_steps)
    return 3


def _run_briefing(provider: VisionProvider, config: AgentConfig, logger, backend: Backend) -> int | None:
    """Before acting, have the model restate the task and its plan, and show it
    in a GUI dialog for approval. Returns None to proceed, or an exit code to
    stop (2 = user cancelled, 4 = capture failed)."""
    try:
        raw_png, real_size = backend.capture(config.region)
    except screen.CaptureError as e:
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
