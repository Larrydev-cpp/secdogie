"""Pairing and "My devices" in Studio (devices.py), between two real profiles
over real WebRTC, through a signaling gateway on loopback:

  * inviting asks for the operator passphrase; a wrong one invites nobody;
  * the invited Studio checks the invitation offline, agrees, and both show
    the same six digits; on "admit" it becomes a member, holding an admission
    the inviter's operator signed, and the roster with the inviter's machine;
  * both go online and see each other directly; a note arrives;
  * a member cannot invite (only owners admit).
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

pytest.importorskip("aiortc")

from secdogie_studio.devices import Devices, qr_rows  # noqa: E402
from secdogie_studio.profile import create_profile  # noqa: E402
from secdogie_transport.pairing import verify_admission  # noqa: E402
from secdogie_transport.signal_server import serve_signaling  # noqa: E402

PASS = "correct horse battery"


@pytest.fixture
def gateway():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    server = asyncio.run_coroutine_threadsafe(serve_signaling(), loop).result(10)
    yield server.url
    asyncio.run_coroutine_threadsafe(server.close(), loop).result(10)
    loop.call_soon_threadsafe(loop.stop)


def until(check, timeout=30.0):
    end = time.monotonic() + timeout
    while not check():
        if time.monotonic() > end:
            raise AssertionError("timed out waiting")
        time.sleep(0.05)


def test_two_studios_pair_then_meet_online(tmp_path, gateway):
    desk = Devices(create_profile(tmp_path / "desk", PASS.encode(), kdf="min"))
    laptop = Devices(create_profile(tmp_path / "laptop", PASS.encode(), kdf="min"))
    desk.set_device_name("书房的电脑")
    laptop.set_device_name("笔记本")
    try:
        desk.write_invite("wrong passphrase", gateway, "https://pages.example/pair.html")
        assert desk.snapshot()["pair"]["phase"] == "ended"

        desk.write_invite(PASS, gateway, "https://pages.example/pair.html")
        pair = desk.snapshot()["pair"]
        assert pair["phase"] == "waiting" and pair["link"].startswith("https://pages.example/pair.html#invite=")
        assert pair["qr"] and set("".join(pair["qr"])) <= {"0", "1"} and 0 < pair["left"] <= 600

        laptop.read_invite(pair["link"])
        card = laptop.snapshot()["join"]
        assert card["phase"] == "card" and card["mesh"] == "home"
        laptop.agree()
        until(lambda: desk.snapshot()["pair"].get("phase") == "compare"
              and laptop.snapshot()["join"].get("phase") == "compare")
        assert desk.snapshot()["pair"]["code"] == laptop.snapshot()["join"]["code"]
        assert desk.snapshot()["pair"]["name"] == "笔记本"
        desk.decide(True)
        until(lambda: desk.snapshot()["pair"].get("phase") == "done" and laptop.snapshot()["join"].get("phase") == "done")

        operator = desk.profile.operator_did()
        member = laptop._state
        assert member["role"] == "member" and member["owners"] == [operator]
        assert verify_admission(member["self"], owners=[operator], mesh="home",
                                device=laptop.profile.node_did()).ok
        assert [a["device"] for a in member["devices"]] == [desk.profile.node_did()]  # the roster
        assert [d["did"] for d in desk.snapshot()["devices"]] == [laptop.profile.node_did()]

        # a member cannot invite
        laptop.write_invite(PASS, gateway, "")
        assert laptop.snapshot()["pair"]["phase"] == "ended"

        desk.go_online(gateway)
        laptop.go_online(gateway)

        def online(d):
            return [x["did"] for x in d.snapshot()["devices"] if x["state"] == "online"]

        until(lambda: online(desk) == [laptop.profile.node_did()] and desk.profile.node_did() in online(laptop),
              timeout=60)
        laptop.say("笔记本来了")
        until(lambda: any(n["text"] == "笔记本来了" for n in desk.snapshot()["notes"]))
    finally:
        desk.close()
        laptop.close()


def test_codes_that_differ_admit_nobody(tmp_path, gateway):
    desk = Devices(create_profile(tmp_path / "desk", PASS.encode(), kdf="min"))
    phone = Devices(create_profile(tmp_path / "phone", PASS.encode(), kdf="min"))
    try:
        desk.write_invite(PASS, gateway, "")
        pair = desk.snapshot()["pair"]
        assert pair["link"] == "" and pair["code_text"]  # no pairing page set: the code alone travels
        phone.read_invite(pair["code_text"])
        phone.agree()
        until(lambda: desk.snapshot()["pair"].get("phase") == "compare")
        desk.decide(False)
        until(lambda: desk.snapshot()["pair"].get("phase") == "ended" and phone.snapshot()["join"].get("phase") == "ended")
        assert desk.snapshot()["devices"] == [] and phone._state["role"] == "owner"
    finally:
        desk.close()
        phone.close()


def test_an_altered_invitation_is_refused_before_anything_connects(tmp_path, gateway):
    desk = Devices(create_profile(tmp_path / "desk", PASS.encode(), kdf="min"))
    try:
        desk.read_invite("https://pages.example/pair.html#invite=not-an-invitation")
        assert desk.snapshot()["join"]["phase"] == "ended"
    finally:
        desk.close()


def test_the_qr_code_is_square_and_binary():
    rows = qr_rows("https://pages.example/pair.html#invite=abc")
    assert len(rows) == len(rows[0]) and set("".join(rows)) == {"0", "1"}
