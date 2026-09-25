"""The vertical slice runs end to end and every stage holds.

Run it once (it stands up a real 3-node mesh on 127.0.0.1) and assert against the
result. Where the agent + desktop packages are installed, the same run exercises
the real structural agent loop; the CI demo job installs them, so that path runs
in CI, and it is asserted only when present."""
from __future__ import annotations

import importlib.util

import pytest

pytest.importorskip("nacl")

from secdogie_demo import run_slice  # noqa: E402


@pytest.fixture(scope="module")
def sliced():
    return run_slice()


def test_every_stage_passes(sliced):
    failed = [(name, detail) for name, ok, detail in sliced.steps if not ok]
    assert not failed, f"stages failed: {failed}"
    assert sliced.ok


def test_stage_details(sliced):
    detail = {name: d for name, _ok, d in sliced.steps}
    # 2: exactly the granted scopes reached A
    assert "observe.read" in detail["2. operator grant reaches A"]
    # 4: evidence is byte-identical on C
    assert "byte" in detail["4. grant + knowledge + evidence converge to C (byte-identical)"].lower()
    # 5: Socratic revised the unattended instruction; click ran, type was refused
    s5 = next(d for n, _o, d in sliced.steps if n.startswith("5."))
    assert "revised=True" in s5
    assert "left_click" in s5 and "physical.type" in s5
    # 6: the replicated run record verifies on C
    assert "verify=True" in next(d for n, _o, d in sliced.steps if n.startswith("6."))


_HAVE_FULL = all(importlib.util.find_spec(m) for m in ("secdogie_agent", "secdogie_desktop"))


@pytest.mark.skipif(not _HAVE_FULL, reason="agent + desktop not installed (real loop path)")
def test_real_structural_loop_is_exercised(sliced):
    assert sliced.used_real_loop is True
