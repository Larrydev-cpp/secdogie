"""WebRTC from Python (rtc.py, signal_server.py, pairing_session.py, mesh_rtc.py),
end to end over loopback through a real signaling gateway:

  * two peers meet in a room and both see the same connection (cross-matched
    DTLS fingerprints);
  * a whole pairing: the codes match on both sides, the admission verifies, and
    the roster reaches the new device; "they differ" admits nothing;
  * three devices form a full mesh: a device learns of another from the roster
    and links to it, notes arrive, a device that leaves shows offline.

Needs aiortc and websockets (secdogie-transport[webrtc]); skipped without them.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiortc")
pytest.importorskip("websockets")

from secdogie_identity import Identity  # noqa: E402
from secdogie_transport.mesh_rtc import OFFLINE, ONLINE, MeshNode  # noqa: E402
from secdogie_transport.pairing import create_admission, create_invite  # noqa: E402
from secdogie_transport.pairing_session import (  # noqa: E402
    PairingCancelled,
    invite_device,
    join_with_invite,
)
from secdogie_transport.rtc import RtcPeer  # noqa: E402
from secdogie_transport.signal_server import sanitize_payload, serve_signaling  # noqa: E402


def run(coro, timeout=60):
    return asyncio.run(asyncio.wait_for(coro, timeout))


async def until(check, timeout=20.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not check():
        if loop.time() > end:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.05)


def test_the_gateway_rebuilds_payloads_from_the_standard_fields_only():
    assert sanitize_payload("offer", {"type": "offer", "sdp": "v=0", "x": 1}) == {"type": "offer", "sdp": "v=0"}
    assert sanitize_payload("offer", {"type": "answer", "sdp": "v=0"}) is None
    assert sanitize_payload("ice-candidate", {"candidate": "c", "sdpMLineIndex": True}) == {
        "candidate": "c", "sdpMid": None, "sdpMLineIndex": None, "usernameFragment": None}
    assert sanitize_payload("ice-candidate", ["c"]) is None


def test_two_peers_meet_in_a_room_and_see_the_same_connection():
    async def go():
        srv = await serve_signaling()
        a, b = RtcPeer(srv.url, "room-1"), RtcPeer(srv.url, "room-1")
        first = asyncio.create_task(a.open(20))
        await asyncio.sleep(0.2)
        await b.open(20)
        await first
        la, ra = a.fingerprints()
        lb, rb = b.fingerprints()
        assert la and ra and (la, ra) == (rb, lb)
        await b.send("from b")
        assert await a.recv(5) == "from b"
        # a third peer finds the room full
        c = RtcPeer(srv.url, "room-1")
        with pytest.raises(Exception, match="room"):
            await c.open(5)
        for p in (a, b):
            await p.close()
        await srv.close()

    run(go())


def _invite(owner, url, ttl=600):
    import secrets

    return create_invite(owner, "home", room=f"pair-{secrets.token_hex(16)}", signal=url, ttl=ttl)


def test_a_whole_pairing_between_two_python_devices():
    owner, phone, desk = Identity.generate(), Identity.generate(), Identity.generate()

    async def go():
        srv = await serve_signaling()
        invite = _invite(owner, srv.url)
        desk_adm = create_admission(owner, "home", desk.did, invite_id="0" * 32, name="书房的电脑")
        seen = {}

        async def decide():
            await until(lambda: "owner" in seen and "phone" in seen)
            return seen["owner"].code == seen["phone"].code

        inviter = asyncio.create_task(invite_device(
            owner, invite, my_name="", on_code=lambda c: seen.__setitem__("owner", c), decide=decide,
            roster={"mesh": "home", "admissions": [desk_adm]}))
        await asyncio.sleep(0.2)
        admission, roster = await join_with_invite(phone, invite, my_name="我的手机",
                                                   on_code=lambda c: seen.__setitem__("phone", c))
        given = await inviter
        assert admission == given and admission["device"] == phone.did and admission["name"] == "我的手机"
        assert seen["owner"].code == seen["phone"].code and len(seen["owner"].code) == 6
        assert seen["owner"].did == phone.did and seen["phone"].did == owner.did
        assert roster == {"mesh": "home", "admissions": [desk_adm]}
        await srv.close()

    run(go())


def test_they_differ_admits_nothing():
    owner, phone = Identity.generate(), Identity.generate()

    async def go():
        srv = await serve_signaling()
        invite = _invite(owner, srv.url)

        async def decide():
            return False

        inviter = asyncio.create_task(invite_device(owner, invite, decide=decide))
        await asyncio.sleep(0.2)
        with pytest.raises(PairingCancelled):
            await join_with_invite(phone, invite)
        with pytest.raises(PairingCancelled):
            await inviter
        await srv.close()

    run(go())


def test_three_devices_form_a_full_mesh():
    owner, phone, tablet = Identity.generate(), Identity.generate(), Identity.generate()
    adm_phone = create_admission(owner, "home", phone.did, invite_id="1" * 32, name="我的手机")
    adm_tablet = create_admission(owner, "home", tablet.did, invite_id="2" * 32, name="平板")

    async def go():
        srv = await serve_signaling()
        notes = []
        common = {"mesh": "home", "owners": [owner.did], "signal_url": srv.url}
        at_owner = MeshNode(owner, admissions=[adm_phone, adm_tablet], name="书房的电脑", **common)
        # the phone was paired first: it knows only the owner
        at_phone = MeshNode(phone, admission=adm_phone, name="我的手机", on_note=notes.append, **common)
        at_tablet = MeshNode(tablet, admission=adm_tablet, admissions=[adm_phone], name="平板", **common)
        assert [d["did"] for d in at_phone.snapshot()["devices"]] == []  # the owner key, unseen yet
        for node in (at_owner, at_phone, at_tablet):
            await node.start()

        def online(node):
            return sorted(d["did"] for d in node.snapshot()["devices"] if d["state"] == ONLINE)

        await until(lambda: len(online(at_owner)) == 2 and len(online(at_phone)) == 2 and len(online(at_tablet)) == 2,
                    timeout=40)
        assert tablet.did in online(at_phone)  # learned from the owner's roster
        names = {d["did"]: d["name"] for d in at_phone.snapshot()["devices"]}
        assert names[owner.did] == "书房的电脑" and names[tablet.did] == "平板"
        assert await at_tablet.say("平板问好") == 2
        await until(lambda: any(n["text"] == "平板问好" for n in notes))
        await until(lambda: any(d["rtt"] is not None for d in at_owner.snapshot()["devices"]), timeout=15)
        await at_tablet.stop()
        await until(lambda: {d["did"]: d["state"] for d in at_owner.snapshot()["devices"]}[tablet.did] == OFFLINE,
                    timeout=30)
        for node in (at_owner, at_phone):
            await node.stop()
        await srv.close()

    run(go(), timeout=120)


def test_a_device_without_an_admission_is_refused():
    owner, stranger = Identity.generate(), Identity.generate()
    other_owner = Identity.generate()
    fake = create_admission(other_owner, "home", stranger.did, invite_id="3" * 32)

    async def go():
        srv = await serve_signaling()
        at_owner = MeshNode(owner, mesh="home", owners=[owner.did], signal_url=srv.url)
        at_owner.roster.admit(fake)  # not ours: not kept, so the owner never even tries
        assert at_owner.roster.devices() == [owner.did]
        # the stranger knows the owner and tries anyway, with an admission from someone else
        at_stranger = MeshNode(stranger, mesh="home", owners=[owner.did], admission=fake, signal_url=srv.url)
        assert at_stranger.roster.devices() == [owner.did]
        await at_owner.start()
        await at_stranger.start()
        await asyncio.sleep(3)
        assert all(d["state"] != ONLINE for d in at_stranger.snapshot()["devices"])
        await at_owner.stop()
        await at_stranger.stop()
        await srv.close()

    run(go())
