"""The floor mirrors constants that live in the running code. If one of those
changes, this fails until floor.py (and the TypeScript copy, via the vectors)
moves with it."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from secdogie_policy import FLOOR

ROOT = Path(__file__).resolve().parents[2]


def test_floor_matches_the_citadel_gates():
    socratic = pytest.importorskip("secdogie_citadel.socratic")
    from secdogie_citadel.action_gate import GateContext
    from secdogie_citadel.consolidate import DEFAULT_MIN_RUNS

    assert FLOOR["instruction.max_len"] == socratic._MAX_LEN
    assert FLOOR["instruction.max_steps"] == socratic._MAX_STEPS
    ctx = GateContext()
    assert FLOOR["plan.repeat_threshold"] == ctx.repeat_threshold
    assert FLOOR["plan.poll_window"] == ctx.poll_window
    assert FLOOR["plan.known_failure_min_runs"] == DEFAULT_MIN_RUNS


def test_floor_matches_the_sandbox_fetch_limits():
    src = (ROOT / "symbiont" / "src" / "sandbox" / "policy.ts").read_text(encoding="utf-8")
    body = re.search(r"HARD_MAX_BODY_BYTES = (\d+) << (\d+);", src)
    timeout = re.search(r"HARD_MAX_TIMEOUT_MS = ([\d_]+);", src)
    assert FLOOR["ingest.max_body_bytes"] == int(body.group(1)) << int(body.group(2))
    assert FLOOR["ingest.timeout_ms"] == int(timeout.group(1).replace("_", ""))
