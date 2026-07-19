"""The core agent loop: screenshot -> model picks one action -> confirm ->
execute -> feed the result back -> repeat, until the model says `done`, the
user stops it, or `max_steps` is hit.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from . import actions, dialog, elements, safety, screen
from .backend import Backend, DesktopBackend, ElementAware
from .macro import Macro, MacroRecorder, MacroStep, resolve_replay_step
from .memory import Memory, SecretRefused
from .plan import Plan
from .providers.base import HistoryStep, VisionProvider
from .trace import ExecutionTrace

# Benign actions that never need a confirmation prompt -- they don't touch the
# mouse/keyboard in a way that can do harm.
_BENIGN = {"wait", "screenshot"}

# Actions that are safe to auto-repeat when they appear to have had no effect:
# re-clicking an unresponsive spot or re-scrolling is harmless. Deliberately
# EXCLUDES type/key/hold_key/drag/open -- re-sending those double-types text,
# re-presses Enter/submits, or re-opens things, so a "no visible change" there
# is reported to the model but never blindly retried.
_RETRY_SAFE = {"left_click", "right_click", "double_click", "move", "scroll"}

# Appended to an action's result when the screen didn't visibly change, so the
# model treats it as a miss and picks a different target/approach (the prompt
# tells it to). Kept as a stable phrase the prompt can reference.
_NO_CHANGE_NOTE = (
    " (no visible change detected after this action -- the target may be wrong or the UI "
    "is blocked; try a different target or approach)"
)

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

# Injected when --memory is on, so the model knows it can persist facts. Its
# recalled memories (memory.render()) are appended separately, below this.
_MEMORY_DIRECTIVE = """\
You have a persistent memory that survives across runs. To save a durable fact \
for your future self -- where a control is, a preference the user confirmed, how \
far you got on a long job -- reply with \
{"action": "remember", "text": "the fact", "key": "optional_stable_name"}. Reuse \
a "key" to update that fact; omit it for a one-off note. NEVER store passwords, \
tokens, card numbers, or other secrets -- this memory is plaintext on disk."""


@dataclass
class AgentConfig:
    task: str
    max_steps: int = 50
    auto: bool = False  # if False (default), every action needs a y/N confirmation
    # High-risk kinds (actions.HIGH_RISK_KINDS -- e.g. `open`, which launches an
    # arbitrary file/URL) require a confirmation *even under --auto*. Default on:
    # --auto trusts the model to click/type unattended, but not to launch things
    # outside the screen sandbox without a human ok. Set False (CLI --allow-risky,
    # or a session that already consented like open/) to run those unattended too.
    confirm_high_risk: bool = True
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
    # After a mutating action, re-screenshot and check whether anything visibly
    # changed (screen.changed_ratio). A click/scroll that changed nothing likely
    # missed (wrong target, window not focused, blocked UI): retry the
    # idempotent ones, and if still nothing, tell the model so it can change
    # strategy instead of repeating a dead action. 0 disables the whole check.
    verify_actions: bool = True
    verify_threshold: float = 0.005  # changed-pixel fraction at/above which an action "did something"
    action_retries: int = 1  # extra attempts for a no-effect *idempotent* action (clicks/scroll/move)
    region: tuple[int, int, int, int] | None = None  # (left, top, width, height); None = full primary monitor
    logger_name: str = "secdogie_agent"  # distinct per concurrent run so loggers don't share/race on handlers
    should_stop: Callable[[], bool] | None = None  # checked each step; lets a caller cancel a running loop
    backend: Backend | None = None  # what to drive; None = the local desktop (mss + pyautogui)
    # Desktop only (ignored when `backend` is set): attach an accessibility provider so the default
    # DesktopBackend becomes element-aware, letting macros anchor to UI-automation identity (the
    # strongest replay tier). Needs the platform a11y lib; off = visual-anchor/coordinate tiers only.
    desktop_ax: bool = False
    # RPA: if set, replay this macro file (zero model calls) with a live fallback the moment a step
    # can't be resolved; a run that finishes with `done` re-saves the full sequence here. See macro.py.
    macro_path: str | None = None
    # Planning: decompose the task into sub-tasks up front and work one at a time (see plan.py). `done`
    # then means "this sub-task is complete" and advances; the run ends when the last one finishes.
    plan: bool = False
    # Error recovery when planning: if a sub-task burns this many steps without a `done`, skip it and
    # move on instead of spinning the whole run. 0 disables (only meaningful with plan=True).
    subtask_step_limit: int = 15
    # Audit: if set, write a tamper-evident hash-chained trace of every step (frame hash + decision +
    # result) to this JSONL path. Verify later with `python -m secdogie_agent.trace <path>`. See trace.py.
    trace_path: str | None = None
    # Memory: if set, give the agent persistent cross-run memory in this SQLite file. The model saves
    # durable facts with a `remember` action and they're recalled into its prompt on later runs (see
    # memory.py). None = stateless (the default). Plaintext -- never have it store secrets.
    memory_path: str | None = None


def run(provider: VisionProvider, config: AgentConfig) -> int:
    """Returns a process-style exit code: 0 done, 1 provider error,
    2 user declined to continue past an ask_user, 3 max_steps exhausted (or a
    plan finished with skipped sub-tasks), 4 no graphical display available to
    screenshot, 5 stopped via should_stop, 6 stalled (same action, unchanged
    screen, stall_limit times)."""
    logger = safety.setup_logging(config.log_path, name=config.logger_name)
    logger.info("task: %s", config.task)
    if config.auto:
        logger.warning("running with --auto: actions execute without per-step confirmation")
    if config.dry_run:
        logger.info("running with --dry-run: actions will be logged but not executed")

    if config.backend is not None:
        backend: Backend = config.backend
    else:
        # Build the default desktop backend, optionally element-aware. The a11y
        # provider is on-machine (reads the live UI-automation tree); if it isn't
        # available make_desktop_ax_provider logs a hint and returns None, so the
        # backend just isn't element-aware -- no failure.
        ax_provider = None
        if config.desktop_ax:
            from . import desktop_ax

            ax_provider = desktop_ax.make_desktop_ax_provider(logger)
        backend = DesktopBackend(
            move_duration=config.move_duration, settle=config.settle, ax_provider=ax_provider
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

    # Planning: decompose the task into sub-tasks up front and work one at a time.
    # Skipped in watch mode (a variable-length trigger doesn't fit a fixed plan).
    plan: Plan | None = None
    if config.plan and not config.watch:
        plan = _build_plan(provider, config, logger, backend)
    subtask_started = 1  # the step the current sub-task began on (for its budget)

    # Audit: a tamper-evident hash chain of every decision, written as it happens.
    trace = ExecutionTrace(config.trace_path) if config.trace_path else None
    if trace is not None:
        logger.info("writing a verifiable execution trace to %s", config.trace_path)

    history: list[HistoryStep] = []
    # Stall detection: the signature + captured-frame hash of the last executed
    # action, and how many times in a row the same action met an unchanged screen.
    prev_exec_sig: tuple | None = None
    prev_exec_frame: bytes | None = None
    stall_count = 0

    # Memory: a SQLite-backed store of durable facts the model saves with the
    # `remember` action; recalled into its prompt below. None = stateless.
    memory = Memory(config.memory_path) if config.memory_path else None
    if memory is not None:
        logger.info("memory: reading/writing durable facts at %s", config.memory_path)

    try:
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

            # Error recovery: a sub-task that won't finish shouldn't burn the whole
            # run. If the current one has spent its step budget without a `done`,
            # skip it and move on -- the next step will show the following sub-task.
            if plan is not None and not plan.is_done and config.subtask_step_limit:
                if step - subtask_started >= config.subtask_step_limit:
                    logger.warning(
                        "sub-task exceeded %d steps without completing; skipping: %s",
                        config.subtask_step_limit, plan.current,
                    )
                    plan.skip_current()
                    subtask_started = step
                    if plan.is_done:
                        logger.warning("plan ended with %d skipped sub-task(s)", len(plan.skipped))
                        return 3
                    continue

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
                resolved = resolve_replay_step(candidate, backend, real_size, frame_png=raw_png)
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
                # When planning, carry the plan + progress into the prompt so the
                # model works the current sub-task and knows where it is (state) --
                # rather than re-deriving the whole job from the last 10 actions.
                step_task = effective_task
                if plan is not None and not plan.is_done:
                    step_task = f"{effective_task}\n\n{plan.progress_note()}"
                if memory is not None:
                    # Rebuilt each step, so a fact remembered earlier this run is
                    # visible on the next one -- not just on a future run.
                    step_task += f"\n\n{_MEMORY_DIRECTIVE}"
                    recalled = memory.render()
                    if recalled:
                        step_task += f"\n\nWhat you remember from earlier runs:\n{recalled}"
                # Element-aware desktop (--desktop-ax): offer the model the current
                # interactable elements so it can click one by identity
                # (click_element) instead of guessing a pixel. Cache THIS step's
                # targets so the ref the model returns resolves against exactly the
                # list it was shown, even if the UI shifts meanwhile. No provider /
                # nothing interactable -> no listing, and the pixel path is unchanged.
                step_targets: list = []
                if isinstance(backend, ElementAware):
                    step_targets = backend.element_targets()
                    listing = elements.render_for_model(step_targets)
                    if listing:
                        step_task += f"\n\n{listing}"
                try:
                    action = provider.next_action(step_task, model_png, model_size, history)
                except Exception as e:
                    logger.error("provider failed to produce an action: %s", e)
                    return 1
                action = action.scaled(scale)
                if config.region is not None:
                    # Model coordinates are relative to the captured region; shift
                    # back to absolute screen coordinates before anything downstream
                    # (confirmation prompts, execution) sees them.
                    action = action.translated(config.region[0], config.region[1])
                # Turn an element-targeted click into a concrete left_click at the
                # element's real-pixel centre. Element bounds are already absolute
                # screen pixels, so this runs AFTER scale/translate (both no-ops on
                # a click_element, which carries no x/y). A ref that doesn't resolve
                # is reported to the model as a miss -- never clicked at a guessed or
                # zero coordinate.
                if action.kind == "click_element":
                    point = elements.point_for_ref(step_targets, action.element)
                    if point is None:
                        logger.warning("click_element: unresolved element ref %r", action.element)
                        history.append(HistoryStep(
                            action=action,
                            result=(
                                f"could not find element {action.element!r} in the listing; "
                                'use a ref shown there (e.g. "e2") or a coordinate action instead'
                            ),
                        ))
                        continue
                    action = replace(action, kind="left_click", x=point[0], y=point[1])

            reasoning = action.reasoning or action.raw.get("reasoning", "")

            # Loop vars are bound as defaults so this closure snapshots THIS step's
            # action/frame/reasoning (it's only ever called within the same step).
            def record_result(result: str, *, action=action, raw_png=raw_png, reasoning=reasoning) -> None:
                """Append the step's outcome to history and, if auditing, to the
                tamper-evident trace -- both keyed to this step's action, the frame
                it was decided on, and the model's reasoning."""
                history.append(HistoryStep(action=action, result=result))
                if trace is not None:
                    trace.record(
                        raw_png,
                        {"kind": action.kind, "x": action.x, "y": action.y, "to_x": action.to_x,
                         "to_y": action.to_y, "text": action.text, "keys": action.keys, "raw": action.raw},
                        reasoning,
                        result,
                    )

            # In watch mode a "wait" means "trigger not seen yet" -- log quietly and
            # keep watching without a confirmation prompt.
            if config.watch and action.kind == "wait":
                logger.info("watching (step %d): no trigger yet%s", step, f" -- {reasoning}" if reasoning else "")
                record_result("watching, condition not met")
                continue

            logger.info("step %d/%d: %s %s", step, config.max_steps, action.kind, f"({reasoning})" if reasoning else "")

            if action.kind == "done":
                # When planning, `done` finishes the CURRENT sub-task, not the run:
                # advance and keep going until the last sub-task is done.
                if plan is not None and not plan.is_done:
                    finished = plan.current
                    plan.complete_current()
                    subtask_started = step
                    logger.info("sub-task complete (%d/%d): %s", len(plan.completed), len(plan.subtasks), finished)
                    if not plan.is_done:
                        record_result("sub-task complete; moving to the next")
                        continue
                    # fall through: the last sub-task just completed -> finish the run

                summary = action.text or action.raw.get("text", "")
                logger.info("done: %s", summary)
                record_result(f"done: {summary}" if summary else "done")
                if plan is not None and plan.skipped:
                    logger.warning("run finished but %d sub-task(s) were skipped: %s", len(plan.skipped), plan.skipped)
                if macro_recorder is not None and macro_recorder.steps:
                    try:
                        macro_recorder.build().save(config.macro_path)
                        logger.info("saved macro: %s (%d step(s))", config.macro_path, len(macro_recorder.steps))
                    except OSError as e:
                        logger.warning("could not save macro %s (%s); the run still succeeded", config.macro_path, e)
                return 0 if (plan is None or not plan.skipped) else 3

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
                record_result("user confirmed, continuing")
                continue

            if action.kind == "remember":
                # Writes a durable fact to the local memory DB -- not an OS action,
                # so it skips confirmation/execution/verification. A `remember`
                # with no --memory file, or an empty/secret value, is a no-op that
                # tells the model why so it doesn't keep retrying.
                value = action.text or action.raw.get("text", "")
                key = action.raw.get("key")
                if memory is None:
                    logger.info("model tried to remember but no memory file is set; ignoring")
                    record_result("memory not enabled; nothing was stored")
                elif not (value or "").strip():
                    record_result("could not remember: the value was empty")
                else:
                    try:
                        stored_key = memory.remember(value, key=key)
                        logger.info("remembered %s", stored_key)
                        record_result(f"remembered ({stored_key})")
                    except SecretRefused:
                        logger.warning("refused to store a value that looked like a secret")
                        record_result(
                            "refused: that looks like a secret and memory is plaintext -- "
                            "do not store passwords/tokens/card numbers"
                        )
                continue

            if config.dry_run:
                logger.info("[dry-run] would execute: %s", action.raw)
                record_result("skipped (dry-run)")
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

            # Risk gate: high-risk kinds (actions.HIGH_RISK_KINDS -- `open` launches
            # a program/URL outside the screen sandbox) still confirm under --auto
            # unless confirm_high_risk was turned off; everything else confirms only
            # when --auto is off. On an unattended run (no TTY) safety.confirm returns
            # False on EOF, so an unconfirmed high-risk action fails closed (skipped),
            # not silently executed.
            is_high_risk = action.kind in actions.HIGH_RISK_KINDS
            force_confirm = is_high_risk and config.confirm_high_risk
            needs_confirm = action.kind not in _BENIGN and (not config.auto or force_confirm)
            if needs_confirm:
                if config.auto and force_confirm:
                    logger.warning("high-risk action '%s' needs confirmation even under --auto", action.kind)
                label = "HIGH-RISK " if is_high_risk else ""
                if not safety.confirm(f"Execute {label}{action.kind}({action.raw})?"):
                    logger.info("action not confirmed, skipping: %s", action.kind)
                    record_result("skipped (user declined)")
                    continue

            executed_ok = False
            try:
                result = backend.execute(action)
                executed_ok = True
            except Exception as e:
                result = f"error: {e}"
                logger.error("action failed: %s", e)

            # Let the UI react before we screenshot (to verify, and before the next
            # step); benign actions (wait) already pace themselves, so they're exempt.
            if config.action_pause > 0 and action.kind not in _BENIGN:
                time.sleep(config.action_pause)

            # Post-action visual verification: did the screen actually change? Retry
            # no-effect idempotent actions, and annotate the result otherwise so the
            # model changes strategy. raw_png is this step's pre-action frame.
            if executed_ok and config.verify_actions and action.kind not in _BENIGN:
                result = _verify_and_maybe_retry(backend, action, raw_png, result, config, logger)

            if executed_ok and macro_recorder is not None:
                if from_replay:
                    macro_recorder.record_step(replayed_step)
                else:
                    macro_recorder.record(action, result, backend, real_size, frame_png=raw_png)
            record_result(result)

        logger.warning("reached max_steps (%d) without the model signaling done", config.max_steps)
        return 3
    finally:
        # Steady-state runtime owns the one connection; close it on every exit
        # path (done, error, stall, max_steps) so a long-lived caller can't leak it.
        if memory is not None:
            memory.close()


def _build_plan(provider: VisionProvider, config: AgentConfig, logger, backend: Backend) -> Plan | None:
    """Ask the provider to decompose the task into sub-tasks (needs one
    screenshot to ground them). Returns a Plan, or None to run unplanned if
    capture/planning fails or the provider returns nothing -- planning must
    never be able to stop a run from starting."""
    try:
        raw_png, real_size = backend.capture(config.region)
    except screen.CaptureError as e:
        logger.warning("could not capture a screenshot to plan (%s); running unplanned", e)
        return None
    model_png, _size, _scale = screen.prepare_for_model(raw_png, real_size, max_edge=config.max_image_edge)
    try:
        subtasks = provider.plan_task(config.task, model_png, real_size)
    except Exception as e:
        logger.warning("could not get a task plan from the model (%s); running unplanned", e)
        return None
    if not subtasks:
        logger.info("no task decomposition returned; running unplanned")
        return None
    logger.info("plan: %d sub-task(s)", len(subtasks))
    for i, sub in enumerate(subtasks, 1):
        logger.info("  %d. %s", i, sub)
    return Plan(subtasks=subtasks)


def _visible_change(pre_png: bytes, post_png: bytes, threshold: float, logger) -> bool:
    """Did the screen change by at least `threshold`? Verification is best-effort:
    if a frame can't be decoded/diffed, assume it changed so a flaky capture never
    triggers a false 'no change' note or a spurious retry."""
    try:
        return screen.changed_ratio(pre_png, post_png) >= threshold
    except Exception as e:
        logger.debug("could not diff frames for verification (%s); assuming changed", e)
        return True


def _verify_and_maybe_retry(backend: Backend, action, pre_png: bytes, result: str, config: AgentConfig, logger) -> str:
    """Check whether a just-executed action visibly changed the screen. If not,
    retry the safe-to-repeat kinds up to `action_retries` times, and if it still
    didn't land, append a note so the model tries something else rather than
    repeating a dead action."""
    try:
        after_png, _ = backend.capture(config.region)
    except screen.CaptureError:
        return result  # can't grab a verify frame -> leave the result untouched

    if _visible_change(pre_png, after_png, config.verify_threshold, logger):
        return result

    if action.kind in _RETRY_SAFE:
        for attempt in range(1, config.action_retries + 1):
            logger.info("action '%s' had no visible effect; retry %d/%d", action.kind, attempt, config.action_retries)
            try:
                result = backend.execute(action)
            except Exception as e:
                logger.error("retry of '%s' failed: %s", action.kind, e)
                return f"error on retry: {e}"
            if config.action_pause > 0:
                time.sleep(config.action_pause)
            try:
                after_png, _ = backend.capture(config.region)
            except screen.CaptureError:
                return result
            if _visible_change(pre_png, after_png, config.verify_threshold, logger):
                return result  # a retry landed

    logger.info("action '%s' produced no visible change; signaling the model", action.kind)
    return result + _NO_CHANGE_NOTE


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
