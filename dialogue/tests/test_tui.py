"""The Textual screen, driven headless with ``App.run_test()``: it shows what the
controller holds, renders node text literally (never as markup), and signs
only the challenge on screen, only after the passphrase prompt."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("nacl")
pytest.importorskip("textual")

from secdogie_citadel.authz import action_hash  # noqa: E402
from secdogie_citadel.lessons import MemoryClass, candidate_id  # noqa: E402
from secdogie_dialogue.app import AppController  # noqa: E402
from secdogie_dialogue.protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    ControlOp,
    ControlPacket,
    DialoguePacket,
    DialogueType,
    Envelope,
    Gate2ChallengePacket,
    Gate2ResponsePacket,
    Header,
    MemoryCandidatePacket,
    NodeDelta,
    NodeOp,
    RiskLevel,
    StateSnapshotPacket,
    TargetAction,
    Verdict,
    kind_of,
)
from secdogie_dialogue.tui import DialogueApp, PassphraseScreen  # noqa: E402
from secdogie_identity import Identity  # noqa: E402

APP, NODE, OPERATOR = Identity.generate(), Identity.generate(), Identity.generate()
DELETE = TargetAction("delete", "f-1", "file", "report.txt", "", True)


class FakeSession:
    def __init__(self):
        self.identity, self.peer_did = APP, NODE.did
        self.sent: list = []
        self.on_envelope = self.on_undeliverable = self.on_peer_down = self.on_peer_up = None

    def send(self, packet, *, reliable=None):
        self.sent.append(packet)

    def close(self):
        pass

    def of(self, cls):
        return [p for p in self.sent if isinstance(p, cls)]


_seq = iter(range(1, 10**9))


def deliver(ctl, packet):
    hdr = Header(PROTOCOL_VERSION, NODE.did, APP.did, "s", next(_seq), 0)
    ctl.on_envelope(Envelope(hdr, kind_of(packet), packet, NODE.did))


def challenge(action=DELETE, claimed=None, expires_in=120.0):
    import time

    return Gate2ChallengePacket("ch1", action, RiskLevel.IRREVERSIBLE, "no way back",
                                claimed or action_hash(action), NODE.did, time.time() + expires_in)


def text_of(app, wid: str) -> str:
    return app.query_one(wid).content.plain


def run(test, *, unlock_with=None):
    """Run ``test(app, pilot, ctl, session)`` inside a headless Textual app."""
    s = FakeSession()
    ctl = AppController(s)
    app = DialogueApp(ctl, unlock_with=unlock_with)

    async def main():
        async with app.run_test(size=(140, 40)) as pilot:
            await test(app, pilot, ctl, s)

    asyncio.run(main())


async def type_line(pilot, line: str):
    await pilot.press(*line) if line else None
    await pilot.press("enter")
    await pilot.pause()


def test_panels_show_the_dialogue_view_and_challenge():
    async def t(app, pilot, ctl, s):
        deliver(ctl, DialoguePacket("p1", DialogueType.SOCRATIC_QUESTION, "Which folder?",
                                    suggested_options=("Downloads", "Desktop")))
        deliver(ctl, StateSnapshotPacket(1, 7, 1, (NodeDelta(NodeOp.ADD, 0, role="AXWindow", name="Drawing"),),
                                         full=True))
        deliver(ctl, challenge())
        app.refresh_panels()
        await pilot.pause()
        assert "Which folder?" in text_of(app, "#convo") and "[2] Desktop" in text_of(app, "#convo")
        assert 'AXWindow "Drawing"' in text_of(app, "#view")
        gate = text_of(app, "#gate")
        assert "IRREVERSIBLE" in gate and "matches" in gate and "/approve or /deny" in gate
        assert "1 to sign" in text_of(app, "#status")

    run(t)


def test_node_text_is_rendered_literally_never_as_markup():
    async def t(app, pilot, ctl, s):
        evil = TargetAction("delete", "f-1", "file", "[bold red]OK[/] [link=http://x]y[/link]", "", True)
        deliver(ctl, challenge(evil))
        deliver(ctl, DialoguePacket("p1", DialogueType.SOCRATIC_QUESTION, "[reverse]trust me[/reverse]"))
        app.refresh_panels()
        await pilot.pause()
        assert "[bold red]OK[/]" in text_of(app, "#gate")
        assert "[reverse]trust me[/reverse]" in text_of(app, "#convo")
        assert app.query_one("#gate").content.spans == []  # no styling smuggled in

    run(t)


def test_typing_answers_the_oldest_question():
    async def t(app, pilot, ctl, s):
        deliver(ctl, DialoguePacket("p1", DialogueType.SOCRATIC_QUESTION, "Which folder?",
                                    suggested_options=("Downloads", "Desktop")))
        await type_line(pilot, "2")
        (ans,) = s.of(DialoguePacket)
        assert ans.in_reply_to == "p1" and ans.content == "Desktop"
        await type_line(pilot, "hello")
        assert "no open question" in app.message and len(s.of(DialoguePacket)) == 1

    run(t)


def test_approve_asks_for_the_passphrase_then_signs_the_challenge_on_screen():
    seen = []

    def unlock_with(passphrase):
        seen.append(passphrase)
        return OPERATOR

    async def t(app, pilot, ctl, s):
        deliver(ctl, challenge())
        app.refresh_panels()
        await type_line(pilot, "/approve")
        assert isinstance(app.screen, PassphraseScreen) and s.sent == []  # nothing signed before the prompt
        await type_line(pilot, "correct horse")
        assert seen == [b"correct horse"]
        (resp,) = s.of(Gate2ResponsePacket)
        assert resp.user_verdict is Verdict.APPROVE and resp.challenge_id == "ch1"
        assert "approved" in app.message and ctl.challenges() == ()

    run(t, unlock_with=unlock_with)


def test_escape_cancels_the_approval():
    async def t(app, pilot, ctl, s):
        deliver(ctl, challenge())
        app.refresh_panels()
        await type_line(pilot, "/approve")
        await pilot.press("escape")
        await pilot.pause()
        assert s.sent == [] and "cancelled" in app.message and len(ctl.challenges()) == 1

    run(t, unlock_with=lambda p: OPERATOR)


def test_an_unsignable_challenge_never_prompts():
    async def t(app, pilot, ctl, s):
        deliver(ctl, challenge(claimed="00" * 32))
        app.refresh_panels()
        await type_line(pilot, "/approve")
        assert not isinstance(app.screen, PassphraseScreen)
        assert "refused" in app.message and "does not match" in app.message and s.sent == []
        assert "/approve" not in text_of(app, "#gate")  # only Deny is offered
        await type_line(pilot, "/deny")
        (resp,) = s.of(Gate2ResponsePacket)
        assert resp.user_verdict is Verdict.DENY

    run(t, unlock_with=lambda p: OPERATOR)


def test_approve_without_a_keystore_or_a_challenge_is_refused():
    async def t(app, pilot, ctl, s):
        await type_line(pilot, "/approve")
        assert "no challenge on screen" in app.message
        deliver(ctl, challenge())
        app.refresh_panels()
        await type_line(pilot, "/approve")
        assert "no operator keystore" in app.message and s.sent == []

    run(t)


def test_a_wrong_passphrase_signs_nothing():
    def unlock_with(passphrase):
        raise ValueError("wrong passphrase, or the keystore was tampered with")

    async def t(app, pilot, ctl, s):
        deliver(ctl, challenge())
        app.refresh_panels()
        await type_line(pilot, "/approve")
        await type_line(pilot, "nope")
        assert s.sent == [] and "not signed" in app.message and len(ctl.challenges()) == 1

    run(t, unlock_with=unlock_with)


def test_memory_offer_and_goal_commands():
    async def t(app, pilot, ctl, s):
        mid = candidate_id(MemoryClass.FACT, "global", "report-folder", "reports go to ~/Reports")
        deliver(ctl, MemoryCandidatePacket(mid, "fact", "global", "report-folder", "reports go to ~/Reports",
                                           "model"))
        app.refresh_panels()
        await pilot.pause()
        assert "report-folder" in text_of(app, "#gate") and "/confirm" in text_of(app, "#gate")
        await type_line(pilot, "/confirm")
        await type_line(pilot, "/goal tidy the desktop")
        await type_line(pilot, "/pause g1")
        await type_line(pilot, "/bogus")
        ops = [p.op for p in s.of(ControlPacket)]
        assert ops == [ControlOp.CONFIRM_MEMORY, ControlOp.ADD_GOAL, ControlOp.PAUSE]
        assert "unknown command" in app.message

    run(t)


def test_the_panels_keep_updating_under_the_passphrase_prompt():
    async def t(app, pilot, ctl, s):
        deliver(ctl, challenge())
        app.refresh_panels()
        await type_line(pilot, "/approve")
        assert isinstance(app.screen, PassphraseScreen)
        deliver(ctl, DialoguePacket("p9", DialogueType.SOCRATIC_QUESTION, "Still there?"))
        app.refresh_panels()  # a timer tick while the prompt is the active screen
        assert "Still there?" in app.screen_stack[0].query_one("#convo").content.plain
        await pilot.press("escape")
        await pilot.pause()
        assert s.sent == []

    run(t, unlock_with=lambda p: OPERATOR)


def test_a_refresh_during_teardown_draws_nothing_and_does_not_crash():
    async def t(app, pilot, ctl, s):
        await app.screen_stack[0].query_one("#status").remove()  # the screen is being torn down
        app.refresh_panels()
        assert app._panels() is None

    run(t)
