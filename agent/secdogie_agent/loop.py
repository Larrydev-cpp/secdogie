"""The core agent loop: screenshot -> model picks one action -> confirm ->
execute -> feed the result back -> repeat, until the model says `done`, the
user stops it, or `max_steps` is hit.
"""
from __future__ import annotations

import hashlib
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from . import actions, dialog, elements, harness, safety, screen
from .backend import Backend, DesktopBackend, ElementAware
from .macro import Macro, MacroRecorder, MacroStep, resolve_replay_step
from .memory import Memory, SecretRefused
from .plan import Plan
from .providers.base import HistoryStep, VisionProvider, is_transient
from .trace import ExecutionTrace

_BENIGN = {"wait", "screenshot", "look"}
_RETRY_SAFE = {"left_click", "right_click", "double_click", "move", "scroll", "click_element"}
# Keep only the most recent N history steps in memory / for the model. Providers
# already truncate further (last 10); this stops unbounded growth on long runs
# and keeps the "actions so far" text from piling up.
HISTORY_KEEP = 24
_NO_CHANGE_NOTE = (
    " (no visible change detected after this action -- the target may be wrong or the UI "
    "is blocked; try a different target or approach)"
)
_WATCH_DIRECTIVE = (
    "\nMONITORING MODE: You are watching the screen continuously, frame by frame.\n"
    "On each frame, decide whether the situation described in the task has occurred:\n"
    '- If it has NOT occurred yet, reply with {"action": "wait", "reasoning": "..."} '
    "and nothing else -- do not act.\n"
    '- Only when it HAS occurred, perform the appropriate action (e.g. "open" a file, '
    "click, type).\n"
    'Use "done" only if the task is a one-shot that is now fully complete and no more '
    "watching is needed."
)
_STALE_TREE_NOTE = (
    "\n\nNOTE: live accessibility tree was empty this frame; listing is last-known. "
    "Prefer look if the UI may have changed."
)
_MEMORY_DIRECTIVE = (
    "You have a persistent memory that survives across runs. To save a durable fact "
    "for your future self -- where a control is, a preference the user confirmed, how "
    "far you got on a long job -- reply with "
    '{"action": "remember", "text": "the fact", "key": "optional_stable_name"}. Reuse '
    'a "key" to update that fact; omit it for a one-off note. NEVER store passwords, '
    "tokens, card numbers, or other secrets -- this memory is plaintext on disk."
)


@dataclass
class AgentConfig:
    task: str
    max_steps: int = 50
    auto: bool = False
    confirm_high_risk: bool = True
    elevated_allowlist: tuple[str, ...] = ()
    dry_run: bool = False
    read_only: bool = False
    log_path: str | None = None
    max_image_edge: int = screen.DEFAULT_MAX_EDGE
    grid: bool = False
    move_duration: float = actions.DEFAULT_MOVE_DURATION
    settle: float = actions.DEFAULT_SETTLE
    gui: bool = False
    watch: bool = False
    watch_interval: float = 2.0
    action_pause: float = 0.15
    stall_limit: int = 4
    max_transient_retries: int = 4
    transient_backoff_base: float = 2.0
    # Post-action visual verify is useful for clicks that may miss, but expensive
    # (extra capture + diff). Limit the default path to the cheap click-like
    # set; type/key/open rarely need a pixel check and used to pile screenshots.
    verify_actions: bool = True
    verify_threshold: float = 0.005
    action_retries: int = 1
    region: tuple[int, int, int, int] | None = None
    logger_name: str = "secdogie_agent"
    should_stop: Callable[[], bool] | None = None
    backend: Backend | None = None
    activate: Callable[[], bool] | None = None
    initial_focus: Callable[[], bool | None] | None = None
    desktop_ax: bool = False
    macro_path: str | None = None
    plan: bool = False
    subtask_step_limit: int = 15
    trace_path: str | None = None
    memory_path: str | None = None
    require_focus: bool = False


def coalesce_element_targets(live, last) -> tuple[list, list, bool]:
    """Prefer the live accessibility tree. An empty frame must not wipe last-known.

    Returns `(targets_this_step, last_to_keep, used_stale)`. Same contract as
    Atlas `keep_tree` / C++ `last_` — a UIA blip would otherwise drop every
    `click_element` ref the model still holds from the previous listing.
    """
    live_list = list(live or [])
    last_list = list(last or [])
    if live_list:
        return live_list, live_list, False
    return last_list, last_list, bool(last_list)


def _present(backend, logger) -> None:
    present = getattr(backend, "present", None)
    if present is None:
        return
    try:
        if present() is False:
            logger.warning(
                "could not confirm the target window is frontmost; this frame may show "
                "whatever window is actually in front, and actions derived from it may "
                "land there"
            )
    except Exception as e:
        logger.warning("could not present the target window before capture: %s", e)


def run(provider: VisionProvider, config: AgentConfig) -> int:
    """Returns a process-style exit code: 0 done, 1 provider error,
    2 user declined to continue past an ask_user, 3 max_steps exhausted (or a
    plan finished with skipped sub-tasks), 4 no graphical display available to
    screenshot, 5 stopped via should_stop, 6 stalled (same action, unchanged
    screen, stall_limit times), 7 require-focus abort (target window could not
    be confirmed focused)."""
    logger = safety.setup_logging(config.log_path, name=config.logger_name)
    logger.info("task: %s", config.task)
    if config.auto:
        logger.warning("running with --auto: actions execute without per-step confirmation")
    if config.dry_run:
        logger.info("running with --dry-run: actions will be logged but not executed")
    if config.read_only:
        logger.info("running with --read-only: mutating actions (type/key/drag/open/save) are blocked")

    if config.backend is not None:
        backend: Backend = config.backend
    else:
        ax_provider = None
        if config.desktop_ax:
            from . import desktop_ax
            ax_provider = desktop_ax.make_desktop_ax_provider(logger)
        backend = DesktopBackend(
            move_duration=config.move_duration, settle=config.settle, ax_provider=ax_provider,
            activate=config.activate,
        )
    backend.setup(logger)

    if config.gui:
        briefing_rc = _run_briefing(provider, config, logger, backend)
        if briefing_rc is not None:
            return briefing_rc

    effective_task = config.task + _WATCH_DIRECTIVE if config.watch else config.task
    if config.watch:
        logger.info("watch mode: polling every %.1fs until the trigger condition occurs", config.watch_interval)

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
            pass
        except (OSError, ValueError) as e:
            logger.warning("could not load macro %s (%s); running live instead", config.macro_path, e)

    plan: Plan | None = None
    if config.plan and not config.watch:
        plan = _build_plan(provider, config, logger, backend)
    subtask_started = 1

    trace = ExecutionTrace(config.trace_path) if config.trace_path else None
    if trace is not None:
        logger.info("writing a verifiable execution trace to %s", config.trace_path)

    history: list[HistoryStep] = []
    prev_exec_sig: tuple | None = None
    prev_exec_frame: bytes | None = None
    stall_count = 0

    element_first = config.desktop_ax and isinstance(backend, ElementAware)
    cached_frame: tuple | None = None
    refresh_view = True
    # Token-saving: reuse the last image bytes we actually sent to the model when
    # the raw capture hash is unchanged. Also track when the next frame should
    # be prepared at higher resolution (after "look" or a no-change result).
    last_sent_hash: bytes | None = None
    last_model_frame: tuple | None = None  # (model_png, model_size, scale)
    boost_detail = False
    last_targets: list = []

    memory = Memory(config.memory_path) if config.memory_path else None
    if memory is not None:
        logger.info("memory: reading/writing durable facts at %s", config.memory_path)

    if config.initial_focus is not None:
        try:
            if config.initial_focus() is False:
                if config.require_focus:
                    logger.error(
                        "require-focus: could not bring the target window to the foreground; "
                        "aborting (exit 7). Check the --window title, or pass --no-require-focus "
                        "to continue anyway."
                    )
                    return 7
                logger.warning(
                    "could not bring the target window to the foreground; the run will act on "
                    "whatever window is frontmost instead -- check the --window title, and note "
                    "that Wayland refuses programmatic focus entirely"
                )
        except Exception as e:
            if config.require_focus:
                logger.error(
                    "require-focus: initial focus assertion failed; aborting (exit 7): %s", e
                )
                return 7
            logger.warning("initial focus assertion failed (proceeding anyway): %s", e)

    try:
        for step in range(1, config.max_steps + 1):
            if config.should_stop is not None and config.should_stop():
                logger.info("stopped externally after %d step(s)", step - 1)
                return 5

            if config.watch and step > 1:
                time.sleep(config.watch_interval)

            _present(backend, logger)

            try:
                raw_png, real_size = backend.capture(config.region)
            except screen.CaptureError as e:
                logger.error("%s", e)
                return 4

            frame_hash = hashlib.blake2b(raw_png, digest_size=16).digest()

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

            harness_el = None
            omitted_image = False
            if action is None:
                step_targets: list = []
                listing = ""
                if isinstance(backend, ElementAware):
                    live_targets = backend.element_targets()
                    step_targets, last_targets, stale_tree = coalesce_element_targets(
                        live_targets, last_targets,
                    )
                    listing = elements.render_for_model(step_targets)
                    if stale_tree and listing:
                        listing += _STALE_TREE_NOTE

                screen_unchanged = (
                    last_sent_hash is not None
                    and frame_hash == last_sent_hash
                    and last_model_frame is not None
                    and not refresh_view
                )

                omit_image = element_first and harness.should_omit_screenshot(
                    step_targets, refresh_view=refresh_view, boost_detail=boost_detail
                )
                if omit_image:
                    # Structured-first: the listing is the live UI. Don't spend
                    # image tokens; the model can `look` the moment pixels matter.
                    model_png, model_size, scale = None, real_size, 1.0
                    omitted_image = True
                elif element_first and cached_frame is not None and not refresh_view and step_targets:
                    model_png, model_size, scale = cached_frame
                elif screen_unchanged:
                    # Biggest token win: do not re-encode / re-upload an identical frame.
                    model_png, model_size, scale = last_model_frame
                else:
                    # Adaptive resolution: boost only when the model asked for a
                    # fresh look or the previous action produced no visible change
                    # (i.e. we likely need more detail to aim correctly).
                    edge = config.max_image_edge
                    if boost_detail:
                        edge = max(edge, min(1920, int(edge * 1.25)))
                    model_png, model_size, scale = screen.prepare_for_model(
                        raw_png, real_size, max_edge=edge, grid=config.grid
                    )
                    cached_frame = (model_png, model_size, scale)
                    last_model_frame = (model_png, model_size, scale)
                    last_sent_hash = frame_hash
                    boost_detail = False
                refresh_view = False

                step_task = effective_task
                if plan is not None and not plan.is_done:
                    step_task = f"{effective_task}\n\n{plan.progress_note()}"
                if memory is not None:
                    step_task += f"\n\n{_MEMORY_DIRECTIVE}"
                    recalled = memory.render()
                    if recalled:
                        step_task += f"\n\nWhat you remember from earlier runs:\n{recalled}"
                if listing:
                    step_task += f"\n\n{listing}"
                if omitted_image:
                    step_task += f"\n\n{harness.OMIT_IMAGE_NOTE}"
                if screen_unchanged:
                    step_task += (
                        "\n\nNOTE: the screen is visually identical to the previous frame "
                        "(no pixel change detected). Prefer wait / look / a different "
                        "target rather than repeating the same click."
                    )
                action = None
                for attempt in range(config.max_transient_retries + 1):
                    try:
                        action = provider.next_action(step_task, model_png, model_size, history)
                        break
                    except Exception as e:
                        if attempt >= config.max_transient_retries or not is_transient(e):
                            logger.error("provider failed to produce an action: %s", e)
                            return 1
                        delay = min(config.transient_backoff_base * (2 ** attempt), 60.0)
                        logger.warning(
                            "model call failed (%s); backing off %.1fs and retrying (%d/%d)",
                            e, delay, attempt + 1, config.max_transient_retries,
                        )
                        time.sleep(delay)
                        if config.should_stop is not None and config.should_stop():
                            logger.info("stopped externally while backing off")
                            return 5
                action = action.scaled(scale)
                if config.region is not None:
                    action = action.translated(config.region[0], config.region[1])
                if omitted_image and action.kind in harness.PIXEL_KINDS:
                    # Model guessed coordinates of an image it was never shown.
                    # Don't click blindly -- ask it to use a listed ref or look.
                    logger.info(
                        "harness: refusing pixel action '%s' on an accessibility-only turn",
                        action.kind,
                    )
                    history.append(HistoryStep(
                        action=action,
                        result=(
                            "no screenshot was sent this step; use click_element / type "
                            "with a listed ref, or look if you need pixels"
                        ),
                    ))
                    if len(history) > HISTORY_KEEP:
                        del history[:-HISTORY_KEEP]
                    refresh_view = True
                    continue
                if action.kind == "click_element" or (action.kind == "type" and action.element):
                    el = elements.resolve_ref(step_targets, action.element)
                    if el is None:
                        logger.warning(
                            "%s: unresolved element ref %r", action.kind, action.element
                        )
                        history.append(HistoryStep(
                            action=action,
                            result=(
                                f"could not find element {action.element!r} in the listing; "
                                'use a ref shown there (e.g. "e2") or a coordinate action instead'
                            ),
                        ))
                        if len(history) > HISTORY_KEEP:
                            del history[:-HISTORY_KEEP]
                        continue
                    harness_el = el
                    if action.kind == "click_element":
                        # Keep kind=click_element so execute can Invoke; stash
                        # the real-pixel centre for the mouse fallback / trace.
                        action = replace(action, x=el.center[0], y=el.center[1])

            reasoning = action.reasoning or action.raw.get("reasoning", "")

            def record_result(result: str, *, action=action, raw_png=raw_png, reasoning=reasoning) -> None:
                history.append(HistoryStep(action=action, result=result))
                if len(history) > HISTORY_KEEP:
                    del history[:-HISTORY_KEEP]
                if trace is not None:
                    trace.record(
                        raw_png,
                        {"kind": action.kind, "x": action.x, "y": action.y, "to_x": action.to_x,
                         "to_y": action.to_y, "text": action.text, "keys": action.keys, "raw": action.raw},
                        reasoning,
                        result,
                    )

            if config.watch and action.kind == "wait":
                logger.info("watching (step %d): no trigger yet%s", step, f" -- {reasoning}" if reasoning else "")
                record_result("watching, condition not met")
                continue

            logger.info("step %d/%d: %s %s", step, config.max_steps, action.kind, f"({reasoning})" if reasoning else "")

            if action.kind == "done":
                if plan is not None and not plan.is_done:
                    finished = plan.current
                    plan.complete_current()
                    subtask_started = step
                    logger.info("sub-task complete (%d/%d): %s", len(plan.completed), len(plan.subtasks), finished)
                    if not plan.is_done:
                        record_result("sub-task complete; moving to the next")
                        continue

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

            if action.kind == "look":
                refresh_view = True
                boost_detail = True  # next prepare uses higher max_edge for the detail the model asked for
                logger.info("step %d: model requested a fresh look%s", step, f" -- {reasoning}" if reasoning else "")
                record_result("will capture a fresh screenshot on the next step")
                continue

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

            # CAD commercial: --read-only blocks anything that can change document/file state
            if config.read_only and actions.is_mutating(action):
                logger.warning("read-only: blocked mutating action '%s'", action.kind)
                record_result("skipped (read-only mode)")
                continue

            if action.kind == "run_elevated":
                from . import elevate
                cmd = (action.path or "").strip()
                if not config.elevated_allowlist:
                    logger.warning("run_elevated ignored: no elevated commands were allowed on this run")
                    record_result(
                        "elevation is off: the operator allowed no elevated commands "
                        "(start with --allow-elevated-command \"<exact command>\" to permit specific ones)"
                    )
                    continue
                if not elevate.is_permitted(cmd, config.elevated_allowlist):
                    logger.warning("run_elevated refused: %r is not in the allowlist", cmd)
                    record_result(
                        f"refused: {cmd!r} is not in this run's elevated allowlist; "
                        "only operator-declared commands can run as SYSTEM"
                    )
                    continue

            if config.stall_limit and action.kind not in _BENIGN:
                sig = (action.kind, action.x, action.y, action.to_x, action.to_y,
                       action.text, tuple(action.keys or ()), action.path)
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

            is_high_risk = actions.is_high_risk(action)
            # High-risk (open/elevated/save/delete/close hotkeys) force confirm even under --auto.
            # Mutating actions under --auto are allowed without extra prompt (operator chose --auto);
            # --read-only already blocked them above.
            force_confirm = is_high_risk and config.confirm_high_risk
            needs_confirm = action.kind not in _BENIGN and (not config.auto or force_confirm)
            if needs_confirm:
                if config.auto and force_confirm:
                    logger.warning("HIGH-RISK action '%s' needs confirmation even under --auto", action.kind)
                label = "HIGH-RISK " if is_high_risk else ""
                if not safety.confirm(f"Execute {label}{action.kind}({action.raw})?"):
                    logger.info("action not confirmed, skipping: %s", action.kind)
                    record_result("skipped (user declined)")
                    continue

            executed_ok = False
            exec_kind = action.kind
            try:
                if action.kind == "run_elevated":
                    from . import elevate
                    r = elevate.run_as_system(action.path)
                    result = f"{r.outcome}: {r.detail}" if r.detail else r.outcome
                    executed_ok = r.outcome == elevate.LAUNCHED
                    (logger.info if executed_ok else logger.warning)("run_elevated -> %s", result)
                else:
                    result, exec_kind = _deliver_action(backend, action, harness_el)
                    executed_ok = True
                    if action.kind == "click_element" and exec_kind == "click_element":
                        logger.info("harness: %s", result)
                    if actions.FOCUS_UNCONFIRMED_NOTE in result:
                        logger.warning(
                            "'%s' ran without confirmed focus on the target window; it may have "
                            "acted on whatever window was in front", action.kind
                        )
                        if config.require_focus:
                            logger.error(
                                "require-focus: action ran without confirmed focus; aborting (exit 7)"
                            )
                            record_result(result)
                            return 7
            except Exception as e:
                result = f"error: {e}"
                logger.error("action failed: %s", e)

            if config.action_pause > 0 and action.kind not in _BENIGN:
                time.sleep(config.action_pause)

            # Only run the extra post-action capture+diff for click-like actions.
            # Type/key/open/drag change the UI less predictably (or not at all in
            # the short window) and were the main source of screenshot pile-up.
            if executed_ok and config.verify_actions and exec_kind in _RETRY_SAFE:
                result = _verify_and_maybe_retry(
                    backend, action, raw_png, result, config, logger, harness_el=harness_el,
                )
                if _NO_CHANGE_NOTE in result:
                    # Next frame needs more detail so the model can re-aim.
                    boost_detail = True

            if executed_ok and macro_recorder is not None:
                if from_replay:
                    macro_recorder.record_step(replayed_step)
                else:
                    macro_recorder.record(action, result, backend, real_size, frame_png=raw_png)
            record_result(result)

        logger.warning("reached max_steps (%d) without the model signaling done", config.max_steps)
        return 3
    finally:
        if memory is not None:
            memory.close()


def _try_harness(backend: Backend, action, el) -> str | None:
    """Deliver click_element / typed-into-a-listed-field via accessibility.

    Returns the result string on success, or None so the caller falls back to
    the mouse/keyboard path. A successful Invoke does not call activate() and
    does not move the cursor -- that's the less-invasive half.
    """
    if el is None:
        return None
    if action.kind == "click_element":
        invoker = getattr(backend, "invoke_element", None)
        return invoker(el) if callable(invoker) else None
    if action.kind == "type":
        setter = getattr(backend, "set_element_value", None)
        return setter(el, action.text or "") if callable(setter) else None
    return None


def _deliver_action(backend: Backend, action, el) -> tuple[str, str]:
    """Execute once, returning (result, exec_kind).

    `click_element` is not a backend verb. First chance is the OS
    accessibility action (`invoke_element`: UIA Invoke / AXPress).

    Windows: on a miss, rewrite to `left_click` (SendInput / pyautogui).
    macOS: never rewrite to a mouse click — pyautogui is Quartz HID
    (`CGEventPost`). AX miss is a refused result, not a HID fallback.
    Linux keeps the mouse rewrite (X11/Wayland), matching the native
    loop which does not mutate at all.

    Retries MUST take this same path — forwarding the raw kind to
    `backend.execute` would raise or no-op on AdbBackend / IosBackend /
    DesktopBackend.
    """
    harnessed = _try_harness(backend, action, el)
    if harnessed is not None:
        return harnessed, action.kind
    exec_action = action
    exec_kind = action.kind
    if action.kind == "click_element":
        if sys.platform == "darwin":
            return (
                "macOS click_element is AXPress only; "
                "HID/CGEvent/IOHID/pyautogui click refused.",
                action.kind,
            )
        exec_action = replace(action, kind="left_click")
        exec_kind = "left_click"
    return backend.execute(exec_action), exec_kind


def _build_plan(provider: VisionProvider, config: AgentConfig, logger, backend: Backend) -> Plan | None:
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
    try:
        return screen.changed_ratio(pre_png, post_png) >= threshold
    except Exception as e:
        logger.debug("could not diff frames for verification (%s); assuming changed", e)
        return True


def _verify_and_maybe_retry(
    backend: Backend, action, pre_png: bytes, result: str, config: AgentConfig, logger,
    *, harness_el=None,
) -> str:
    try:
        after_png, _ = backend.capture(config.region)
    except screen.CaptureError:
        return result

    if _visible_change(pre_png, after_png, config.verify_threshold, logger):
        return result

    if action.kind in _RETRY_SAFE:
        for attempt in range(1, config.action_retries + 1):
            logger.info("action '%s' had no visible effect; retry %d/%d", action.kind, attempt, config.action_retries)
            try:
                result, _kind = _deliver_action(backend, action, harness_el)
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
                return result

    logger.info("action '%s' produced no visible change; signaling the model", action.kind)
    return result + _NO_CHANGE_NOTE


def _run_briefing(provider: VisionProvider, config: AgentConfig, logger, backend: Backend) -> int | None:
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
        logger.warning("could not get a task briefing from the model: %s", e)
        return None

    if not plan:
        return None

    logger.info("task briefing:\n%s", plan)
    if not dialog.confirm_plan(config.task, plan):
        logger.info("user cancelled at the task briefing")
        return 2
    return None
