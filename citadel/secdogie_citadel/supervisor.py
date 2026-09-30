"""The persistent, supervised Citadel node.

Drives goals from the journal: pick a ready goal (its dependencies done), run it,
and record its status/result back as signed events. State lives entirely in the
journal, so a restart REPLAYS it and continues -- done goals are not re-run, and
a goal left `active` by a crash is re-queued (goal-level resume; the agent loop
itself is one-shot, so a re-queued goal is re-run from the top).

Human oversight is preserved, not removed:
  * `run_task` receives a `confirm(prompt, high_risk)` callback. The default
    handler FAILS CLOSED (denies), and the agent loop confirms high-risk steps in
    every mode (there is no switch to turn that off), so a high-risk step blocks
    until a human approves.
    Today that human answers a terminal prompt (`terminal_confirm`); an
    operator-approval path through the fleet / console / desktop does not exist
    yet (planned: Track C3). Nothing here bypasses a gate.
  * The read-only memory wall and the loop's exit-code semantics are untouched.

The `run_task` seam (task, *, should_stop, on_status, confirm) -> (code, summary)
is injected: production wires it to `agent_run_task` (agent.loop.run); tests pass
a fake, so all of this is exercised headlessly with no model, desktop, or network.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryConfig:
    """Staged memory for a supervised node (see MEMORY.zh.md).

    ``candidates_path``: the local S2 quarantine (SQLite). ``min_runs``: failing
    runs before a caution is promoted. ``confirmers``: the operator App session
    keys whose signed confirmations promote facts (None: no fact ever counts).
    ``scope``: which memories this node reads (plus global). ``require_intent``:
    Gate 1 asks a destructive step for its rollback or an explicit
    irreversible."""

    candidates_path: str = ":memory:"
    min_runs: int = 3
    confirmers: Any = None
    scope: str = "global"
    require_intent: bool = True


@dataclass(frozen=True)
class OperatorHooks:
    """How a node reaches its operator (the Dialogue App bridge), as plain
    callables so citadel needs no dialogue import. All optional.

    ``confirm(prompt, high_risk)``: a high-risk step or plan approval.
    ``ask(question)``: the model's ask_user -> the operator's answer text, or
    None when there is none (timeout, peer down). ``authorize(planned)``: a
    destructive action -> an operator-signed Gate 2 token, or None; the gate
    verifies it against ``operators``. ``observe(planned, decision)`` sees every
    gate decision (the bridge uses it to make a fresh signature count as the
    step's confirmation). ``on_targets(elements)`` receives the element targets
    the loop offers the model each step, for the operator's structural view
    (an observer: it cannot change the step)."""

    confirm: Callable[[str, bool], bool] | None = None
    ask: Callable[[str], str | None] | None = None
    authorize: Callable[[Any], dict | None] | None = None
    operators: Any = None
    observe: Callable[[Any, Any], None] | None = None
    on_targets: Callable[[list], None] | None = None


class Supervisor:
    def __init__(
        self,
        journal: Any,
        run_task: Callable[..., tuple[int, str]],
        *,
        max_attempts: int = 1,
        confirm_handler: Callable[[str, bool], bool] | None = None,
        logger: logging.Logger | None = None,
        issuers=None,
        memory: MemoryConfig | None = None,
    ):
        from .goals import build_goal_tree
        from .run import RunRecorder

        self.journal = journal
        self.run_task = run_task
        self.max_attempts = max_attempts
        self._confirm_handler = confirm_handler
        self.log = logger or logging.getLogger("secdogie_citadel.supervisor")
        self._build_goal_tree = build_goal_tree
        self.recorder = RunRecorder(journal)
        # Trusted capability issuers (operator DIDs). When set, every action the
        # agent is about to execute is checked against this node's grants.
        self.issuers = issuers
        self.memory = memory
        self._hooks = OperatorHooks()
        self._candidates = None
        if memory is not None:
            from .lessons import CandidateStore

            self._candidates = CandidateStore(memory.candidates_path)
        self._halted = threading.Event()

    # -- halting ---------------------------------------------------------------

    def halt(self, reason: str = "") -> None:
        """Stop this node's work: the running goal sees ``should_stop`` and
        ``run_ready`` schedules nothing further. Used when this node's own DID
        is revoked. Idempotent."""
        if not self._halted.is_set():
            self.log.warning("supervisor halted%s", f": {reason}" if reason else "")
        self._halted.set()

    @property
    def halted(self) -> bool:
        return self._halted.is_set()

    # -- capabilities (2.9) ----------------------------------------------------

    def add_grant(self, signed_grant: dict) -> dict:
        """Carry a signed capability grant in the journal, so it reaches the node
        (and its peers) through normal replication. A forged or untrusted grant is
        harmless here: it simply fails verification in `node_scopes`."""
        return self.journal.append("capability_grant", signed_grant)

    def grants(self) -> list[dict]:
        return [e.get("body") for e in self.journal.events() if e.get("kind") == "capability_grant"]

    def node_scopes(self, now: float | None = None) -> frozenset:
        """The scopes this node currently holds: verified, unexpired grants from a
        trusted issuer whose subject is this node's DID. Empty without issuers."""
        ident = getattr(self.journal, "identity", None)
        if self.issuers is None or ident is None:
            return frozenset()
        from secdogie_identity.capability import effective_scopes

        return effective_scopes(self.grants(), subject=ident.did, issuers=self.issuers, now=now)

    # -- goal authoring ------------------------------------------------------

    def add_goal(self, goal_id: str, title: str = "", deps=()) -> dict:
        return self.journal.append("goal", {"op": "add", "id": goal_id, "title": title, "deps": list(deps)})

    def request_stop(self, goal_id: str) -> dict:
        return self.journal.append("control", {"op": "stop", "goal_id": goal_id})

    def request_pause(self, goal_id: str) -> dict:
        return self.journal.append("control", {"op": "pause", "goal_id": goal_id})

    def resume(self, goal_id: str) -> dict:
        return self.journal.append("control", {"op": "resume", "goal_id": goal_id})

    def set_confirm_handler(self, handler: Callable[[str, bool], bool] | None) -> None:
        self._confirm_handler = handler

    def set_operator_hooks(self, hooks: OperatorHooks | None) -> None:
        """Reach the operator through the Dialogue App bridge. Its ``confirm``
        replaces the plain confirm handler."""
        self._hooks = hooks or OperatorHooks()
        if self._hooks.confirm is not None:
            self._confirm_handler = self._hooks.confirm

    # -- projections ---------------------------------------------------------

    def _tree(self):
        return self._build_goal_tree(self.journal.events())

    def _controls(self) -> tuple[set, set]:
        stops: set = set()
        paused: set = set()
        for e in self.journal.events():
            if e.get("kind") != "control":
                continue
            body = e.get("body") or {}
            gid, op = body.get("goal_id"), body.get("op")
            if op == "stop":
                stops.add(gid)
            elif op == "pause":
                paused.add(gid)
            elif op == "resume":
                paused.discard(gid)
                stops.discard(gid)  # resume clears a stop too, so the goal can run again
        return stops, paused

    def _attempts(self, goal_id: str) -> int:
        return sum(
            1 for e in self.journal.events()
            if e.get("kind") == "result" and (e.get("body") or {}).get("goal_id") == goal_id
        )

    def pending_ready(self) -> list[str]:
        """Ready goals (pending, deps done) that are not paused or stopped.
        A stopped/paused goal stays pending but parked until it is resumed."""
        stops, paused = self._controls()
        blocked = stops | paused
        return [g for g in self._tree().ready() if g not in blocked]

    # -- resume --------------------------------------------------------------

    def recover(self) -> list[str]:
        """Re-queue goals left `active` by a crash so they run again (the agent
        loop is one-shot, so resume is at goal granularity). Returns their ids."""
        requeued = []
        for gid, node in self._tree().nodes.items():
            if node.status == "active":
                self.journal.append("goal", {"op": "update", "id": gid, "status": "pending"})
                requeued.append(gid)
        return requeued

    def _open_runs(self, goal_id: str) -> list:
        """Recovery decisions for this goal's runs that are still non-terminal."""
        from .recovery import recovery_for
        from .run import TERMINAL_STATES
        from .state import StateStore

        store = StateStore()
        store.merge_events(self.journal.events())
        out = []
        for rid, r in sorted(store.entities("run").items()):
            if r.get("goal_id") == goal_id and r.get("state") not in TERMINAL_STATES:
                d = recovery_for(store, rid)
                if d is not None:
                    out.append(d)
        return out

    def recover_runs(self):
        """Crash recovery at run granularity (Phase 2.8): from the materialized
        state, decide how to resume each run left mid-flight and record the
        decision. Crucially, a run that crashed while `executing` is marked
        `reobserve_before_retry` -- the agent must re-observe (did the action
        already happen?) before any retry, so a crash never double-acts. Returns
        the recorded `RecoveryDecision`s. Additive: `recover()` still re-queues the
        goals; this records the safe way to resume their runs."""
        from .recovery import plan_recovery
        from .state import StateStore

        store = StateStore()
        store.merge_events(self.journal.events())
        decisions = plan_recovery(store)
        for d in decisions:
            self.recorder.record_recovery(d.run_id, d.action, from_state=d.from_state)
        return decisions

    # -- staged memory -----------------------------------------------------

    def memory_view(self):
        """Consolidated (S3) memory as this node may use it: memory events from
        authors the journal still trusts, facts only with a verified operator
        confirmation, global plus this node's scope."""
        from .consolidate import build_memory

        cfg = self.memory
        return build_memory(self.journal.events(), trust=getattr(self.journal, "allowlist", None),
                            confirmers=cfg.confirmers if cfg else None, scope=cfg.scope if cfg else None)

    def _active_goal_ids(self) -> frozenset:
        return frozenset(g for g, n in self._tree().nodes.items() if n.status in ("pending", "active"))

    def _remember(self, value: str, key: str | None) -> str:
        c = self._candidates.note(value, key=key, scope=self.memory.scope)
        return c.key

    def _recall(self) -> str:
        return self.memory_view().render()

    def consolidate_memory(self):
        """One S1 -> S2 -> S3 pass over this node's journal. Returns the report,
        or None when memory is off."""
        if self.memory is None:
            return None
        from .consolidate import consolidate
        from .episodes import episodes_from_events

        return consolidate(self.journal, self._candidates, episodes_from_events(self.journal.events()),
                           min_runs=self.memory.min_runs, scope="global",
                           trust=getattr(self.journal, "allowlist", None), confirmers=self.memory.confirmers)

    # -- execution -----------------------------------------------------------

    def _confirm(self, goal_id: str, prompt: str, high_risk: bool) -> bool:
        self.journal.append("confirm_request", {"goal_id": goal_id, "prompt": prompt, "high_risk": high_risk})
        approved = bool(self._confirm_handler(prompt, high_risk)) if self._confirm_handler is not None else False
        self.journal.append("confirm_result", {"goal_id": goal_id, "approved": approved})
        return approved

    def _ask(self, goal_id: str, question: str):
        """The model's question to the operator, and the answer, on the journal."""
        self.journal.append("ask_request", {"goal_id": goal_id, "question": question})
        answer = self._hooks.ask(question) if self._hooks.ask is not None else None
        self.journal.append("ask_result", {"goal_id": goal_id, "answered": answer is not None,
                                           "answer": answer if isinstance(answer, str) else ""})
        return answer

    def run_goal(self, goal_id: str) -> tuple[int, str]:
        node = self._tree().nodes.get(goal_id)
        if node is None:
            raise KeyError(f"no such goal {goal_id!r}")
        stops, paused = self._controls()
        if goal_id in paused:
            return (2, "paused")
        if goal_id in stops:
            self.journal.append("result", {"goal_id": goal_id, "code": 5, "summary": "stopped before start"})
            self.journal.append("goal", {"op": "update", "id": goal_id, "status": "pending"})
            return (5, "stopped")  # parked (excluded from ready until resumed)

        self.journal.append("goal", {"op": "update", "id": goal_id, "status": "active"})
        self.journal.append("status", {"goal_id": goal_id, "state": "running"})

        # Runs of this goal left open by a crash: the new run supersedes them, and
        # an `executing` crash means the agent must check before redoing (2.8).
        from .recovery import REOBSERVE_BEFORE_RETRY

        prior = self._open_runs(goal_id)
        recovery = next((d for d in prior if d.action == REOBSERVE_BEFORE_RETRY), None)

        # Open a run: the agent's steps (observe->gate->execute->verify) get
        # recorded as signed state under this run_id and converge over the mesh.
        run_id = self.recorder.start_run(goal_id)
        for d in prior:
            self.recorder.finish_run(d.run_id, 5, f"superseded by {run_id} after recovery")

        def should_stop() -> bool:
            if self._halted.is_set():
                return True
            s, _ = self._controls()
            return goal_id in s

        def on_status(detail) -> None:
            self.journal.append("status", {"goal_id": goal_id, "detail": str(detail)})

        def confirm(prompt: str, high_risk: bool = True) -> bool:
            return self._confirm(goal_id, prompt, high_risk)

        # With memory on, every gated step is tied to its action's effect hash
        # (for episodic memory), and the gate knows what failed before.
        correlator = None
        known: frozenset = frozenset()
        if self.memory is not None:
            from .loop_memory import StepCorrelator

            correlator = StepCorrelator()
            known = self.memory_view().known_failures

        def record_step(observation=None, action=None, result="", verdict="", state="executing",
                        outcome="") -> str:
            key, findings = correlator.take(action) if correlator is not None else ("", ())
            return self.recorder.record_step(
                run_id, observation=observation, action=action,
                result=result, verdict=verdict, state=state,
                # an outcome only means something for a step the gate judged
                action_key=key, outcome=outcome if key else "", findings=findings,
            )

        # Optional hooks, passed only when in use so older run_task callables that
        # don't accept them keep working.
        extra: dict = {}
        hooks = self._hooks
        if self.issuers is not None or self.memory is not None or hooks.authorize is not None:
            from .loop_gate import make_plan_gate

            instruction = node.title or goal_id
            enforce = self.issuers is not None
            ident = getattr(self.journal, "identity", None)
            subject_did = getattr(ident, "did", "") if ident is not None else ""
            observers = [o for o in (correlator.observe if correlator is not None else None, hooks.observe)
                         if o is not None]

            def observe(planned, decision):
                for o in observers:
                    o(planned, decision)

            def plan_gate(view, recent):
                # Re-read grants and goals on every check, so an expiry, a new
                # grant or a removed goal takes effect mid-run.
                return make_plan_gate(
                    self.node_scopes() if enforce else (), enforce=enforce, instruction=instruction,
                    known_failures=known,
                    active_goal_ids=self._active_goal_ids() if self.memory is not None else (),
                    purpose=goal_id if self.memory is not None else "",
                    require_intent=bool(self.memory is not None and self.memory.require_intent),
                    observer=observe if observers else None,
                    authorize=hooks.authorize, operators=hooks.operators, subject_did=subject_did,
                )(view, recent)

            extra["plan_gate"] = plan_gate
        if self.memory is not None:
            extra["remember"] = self._remember
            extra["recall"] = self._recall
        if hooks.ask is not None:
            extra["ask"] = lambda question: self._ask(goal_id, question)
        if hooks.on_targets is not None:
            extra["on_targets"] = hooks.on_targets
        if recovery is not None:
            extra["recovery"] = {
                "run_id": recovery.run_id,
                "action": recovery.action,
                "reason": recovery.reason,
                "verify_action_id": recovery.verify_action_id,
                "verify_observation_id": recovery.verify_observation_id,
            }

        try:
            code, summary = self.run_task(node.title or goal_id, should_stop=should_stop,
                                          on_status=on_status, confirm=confirm, record_step=record_step,
                                          **extra)
        except Exception as e:  # a crashing task must not wedge the supervisor
            self.log.exception("goal %s crashed", goal_id)
            code, summary = 1, f"error: {e}"

        code = int(code)
        self.recorder.finish_run(run_id, code, summary)
        if self.memory is not None:
            try:
                self.consolidate_memory()
            except Exception:  # memory is an aid: a failed pass never fails the goal
                self.log.exception("memory consolidation after goal %s failed", goal_id)
        self.journal.append("result", {"goal_id": goal_id, "code": code, "summary": str(summary)})
        if code == 0:
            self.journal.append("goal", {"op": "complete", "id": goal_id})
        elif code == 5:
            # stopped mid-run: park it (pending), not failed -- resume re-runs it.
            self.journal.append("goal", {"op": "update", "id": goal_id, "status": "pending"})
        elif self._attempts(goal_id) >= self.max_attempts:
            self.journal.append("goal", {"op": "update", "id": goal_id, "status": "failed"})
        else:
            self.journal.append("goal", {"op": "update", "id": goal_id, "status": "pending"})
        return (code, str(summary))

    def run_ready(self, max_goals: int = 1000) -> list[tuple[str, int, str]]:
        """Run ready goals until none remain (or max_goals). Deterministic: the
        lowest ready id first each round."""
        results: list[tuple[str, int, str]] = []
        for _ in range(max_goals):
            if self._halted.is_set():
                break
            ready = sorted(self.pending_ready())
            if not ready:
                break
            gid = ready[0]
            code, summary = self.run_goal(gid)
            results.append((gid, code, summary))
        return results


def terminal_confirm(prompt: str, high_risk: bool) -> bool:
    """A fail-closed terminal gate: y/N, defaults to No on EOF -- for an operator
    running the node in a foreground terminal. Console/desktop wire their own."""
    tag = "HIGH-RISK " if high_risk else ""
    try:
        answer = input(f"{tag}confirm: {prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def agent_run_task(
    task: str, *, should_stop, on_status, confirm, record_step=None, plan_gate=None, recovery=None,
    remember=None, recall=None, ask=None, on_targets=None,
) -> tuple[int, str]:
    """Production task runner: drive the real agent loop for one goal, keeping the
    high-risk confirmation gate wired to `confirm`. Imports the agent lazily so
    citadel's other paths don't depend on it.

    When `record_step` is given (the Supervisor's run recorder), each hash-chained
    ExecutionTrace entry the loop produces -- frame hash (the observation), the
    action, and the result -- is mirrored into the run's signed state, so the run
    materializes and converges over the mesh.

    `plan_gate` (the node's capability check, see loop_gate) runs before every
    action the loop executes; `recovery` (an interrupted previous run) puts a
    check-before-redoing note in front of the task."""
    import argparse

    from secdogie_agent import cli_common
    from secdogie_agent.loop import AgentConfig, run

    args = argparse.Namespace(
        api_key=None, model=None, config=None, provider=None,
        auto=True, dry_run=False, max_steps=40, log_file=None,
        max_image_edge=None, grid=False, action_pause=None, no_verify=False,
        stall_limit=None, plan=False, watch=False, watch_interval=None, trace=None,
        memory=None, subtask_step_limit=None,
    )
    provider = cli_common.resolve_provider(args, "secdogie-citadel")
    if provider is None:
        return 1, "no API key resolved for the citadel node (set one in its env/config)"

    if recovery:
        from .recovery import recovery_preamble

        task = recovery_preamble(recovery) + task
    cfg_kwargs = cli_common.loop_config_kwargs(args, task=task, backend=None)
    cfg_kwargs["should_stop"] = should_stop
    cfg_kwargs["on_event"] = lambda ev, payload: on_status(f"{ev}: {payload}")
    cfg_kwargs["approve_action"] = lambda prompt, high_risk: confirm(prompt, high_risk)
    # ask_user: through the operator bridge when there is one (the answer text
    # returns to the model), else the plain high-risk confirmation as before.
    cfg_kwargs["ask_operator"] = ask if ask is not None else (lambda question: confirm(question, True))
    # The loop calls approver(task, plan): show the operator the plan, not the task.
    cfg_kwargs["approve_plan"] = lambda task, plan: confirm(f"approve plan: {(plan or '')[:200]}", False)
    if record_step is not None:
        from secdogie_agent.loop import classify_result

        cfg_kwargs["trace_on_entry"] = lambda entry: record_step(
            observation=entry.frame_sha256, action=entry.action, result=entry.result,
            outcome=classify_result(entry.result),
        )
    if plan_gate is not None:
        cfg_kwargs["plan_gate"] = plan_gate
    if remember is not None:
        cfg_kwargs["remember_hook"] = remember
    if recall is not None:
        cfg_kwargs["memory_block"] = recall
    if on_targets is not None:  # the operator's structural view (dialogue's snapshot publisher)
        cfg_kwargs["on_targets"] = on_targets
    code = run(provider, AgentConfig(**cfg_kwargs))
    return code, f"agent exited {code}"
