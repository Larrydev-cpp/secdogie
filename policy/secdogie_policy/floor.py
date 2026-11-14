"""The built-in floor: the values a policy bundle may tighten but never loosen.

Each number is the default the running code already uses, named where it lives,
so a bundle that sets a looser value is refused (``loosens``) rather than
quietly widening a gate. ``tests/test_floor.py`` fails if a number here drifts
from the code it mirrors; the TypeScript validator carries the same table and
the golden vectors check the two agree.

Nothing here touches Gate 2, HITL for high-risk actions, the capability
allowlist or the forbidden list: those are not in any bundle schema at all.
"""
from __future__ import annotations

FLOOR: dict[str, int] = {
    # citadel/socratic.py: _MAX_LEN, _MAX_STEPS
    "instruction.max_len": 600,
    "instruction.max_steps": 6,
    # citadel/action_gate.py: GateContext.repeat_threshold, GateContext.poll_window
    "plan.repeat_threshold": 2,
    "plan.poll_window": 3,
    # citadel/consolidate.py: DEFAULT_MIN_RUNS (failures before an action is "known failed")
    "plan.known_failure_min_runs": 3,
    # Decision 5 in PLAN.md: at most 1 request/s and 1 at a time per origin
    "ingest.rps_milli": 1000,
    "ingest.concurrency": 1,
    # symbiont/src/sandbox/policy.ts: HARD_MAX_BODY_BYTES, HARD_MAX_TIMEOUT_MS
    "ingest.max_body_bytes": 8 << 20,
    "ingest.timeout_ms": 30_000,
}
