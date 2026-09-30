"""The window's whole flow, end to end: the real local backend (node + App over
UDP on 127.0.0.1, the production runner and the real agent loop), driven only
through ``DialogModel`` -- what the window calls. The model and the desktop are
the node suite's stand-ins (``node/tests/fakes.py``).

  1. The operator sends a goal; the loop proposes a high-risk step and an
     approval card appears. The first approval sets the passphrase (typed
     twice), makes the operator key, seals it, and signs; the step runs.
  2. The model asks a question; the operator answers in the composer.
  3. The model's note appears as a memory card; "remember" puts it in S3.
  4. A click that fails in three goals is refused by Gate 1 on the fourth.
  5. The window closes and opens again: the same keys, the same journal and
     memory. A wrong passphrase signs nothing; the right one signs.
"""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("nacl")
pytest.importorskip("secdogie_agent")

import fakes  # noqa: E402
from nacl import pwhash  # noqa: E402
from secdogie_agent import cli_common  # noqa: E402
from secdogie_app.local import LocalBackend  # noqa: E402
from secdogie_app.model import APPROVAL, MEMORY, OPEN, PROBE, DialogError, DialogModel  # noqa: E402
from secdogie_citadel.episodes import episodes_from_events  # noqa: E402

CHEAP = {"opslimit": pwhash.argon2id.OPSLIMIT_MIN, "memlimit": pwhash.argon2id.MEMLIMIT_MIN}
PASS = "correct horse"
DELETE_AGAIN = [{"action": "key", "keys": ["delete"], "rollback": "restore it from the Trash"},
                {"action": "done", "text": "deleted"}]


def until(model, pick, timeout=30.0):
    """Refresh as the window does until ``pick(messages)`` is truthy. (Not
    ``time.sleep``: the stand-ins make it a no-op, and a spinning loop would
    starve the node's threads.)"""
    tick = threading.Event()
    deadline = time.time() + timeout
    while time.time() < deadline:
        model.refresh()
        got = pick(model.messages())
        if got:
            return got
        tick.wait(0.05)
    raise AssertionError(f"timed out; the window shows: {[(m.kind, m.text, m.state) for m in model.messages()]}")


def open_card(kind):
    return lambda msgs: next((m for m in msgs if m.kind == kind and m.state == OPEN), None)


def said(text):
    return lambda msgs: next((m for m in msgs if text in m.text), None)


@pytest.fixture
def scripted(monkeypatch):
    desk, histories = fakes.install(monkeypatch.setattr)
    plans = [list(fakes.FILE_THE_REPORT), *(list(fakes.TIDY_UP) for _ in range(4)), list(DELETE_AGAIN)]
    monkeypatch.setattr(cli_common, "resolve_provider", lambda args, prog: fakes.Scripted(plans.pop(0), histories))
    return desk, histories


def open_window(home):
    backend = LocalBackend(home, kdf=CHEAP, challenge_ttl=30.0, probe_ttl=30.0)
    return backend, DialogModel(backend.start(), backend.operator_key)


def test_the_window_end_to_end(scripted, tmp_path):
    desk, histories = scripted
    home = tmp_path / "home"
    backend, model = open_window(home)
    try:
        # 1. a goal; the high-risk step waits for the operator; the first approval sets the passphrase
        assert model.send("file the report") == "goal"
        card = until(model, open_card(APPROVAL))
        assert "key" in card.text and card.actions == ("approve", "deny")
        assert not model.passphrase_set
        with pytest.raises(DialogError, match="不一致"):
            model.approve(card.ref, PASS, "something else")
        assert not (home / "operator.keystore").exists()
        model.approve(card.ref, PASS, PASS)
        assert model.passphrase_set and backend.operators.dids() == {backend.operator_key.did}

        # 2. the question, answered in the composer
        probe = until(model, open_card(PROBE))
        assert "folder" in probe.text and "folder" in model.composer_hint()
        assert model.send("Desktop") == "answer"

        # 3. the note: remember it
        note = until(model, open_card(MEMORY))
        assert "reports go to ~/Reports" in note.text
        until(model, said("finished: exit 0"))
        model.remember(note.ref)
        until(model, said("accepted: remembered"))

        # 4. the same failing click, three goals running; the fourth is refused before it clicks
        for _ in range(3):
            model.send("tidy up")
        until(model, lambda msgs: sum("finished" in m.text for m in msgs) == 4)
        fourth = model.add_goal("tidy up")
        until(model, said(f"goal {fourth} finished"))
        assert model.active_goals() == []
    finally:
        backend.stop()

    events = backend.node.journal.events()
    episodes = {e.goal_id: e for e in episodes_from_events(events).values()}
    first, *tidies = sorted(episodes)
    delete = episodes[first].steps[1]
    assert delete.outcome == "ok" and desk.done[:2] == ["invoke:File name", "key"]  # ran only once signed
    assert [episodes[g].steps[0].outcome for g in tidies[:3]] == ["failed"] * 3
    refused = episodes[tidies[3]].steps[0]
    assert refused.outcome == "rejected" and any("known-failure" in f for f in refused.findings)
    assert any("Desktop" in str(r) for h in histories for r in h)  # the answer reached the model

    # 5. the window again: same keys, journal and memory; the passphrase guards the next approval
    backend2, model2 = open_window(home)
    try:
        assert backend2.node_identity.did == backend.node_identity.did and model2.passphrase_set
        assert "reports go to ~/Reports" in backend2.node.supervisor._recall()
        model2.send("delete it")
        card = until(model2, open_card(APPROVAL))
        with pytest.raises(DialogError, match="口令不对"):
            model2.approve(card.ref, "not the passphrase")
        assert until(model2, open_card(APPROVAL)).ref == card.ref  # still waiting, nothing signed
        model2.approve(card.ref, PASS)
        until(model2, said("finished: exit 0"))
    finally:
        backend2.stop()
    assert desk.done.count("key") == 2
