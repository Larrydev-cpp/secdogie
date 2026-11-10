"""Your own devices as one mesh: the room each pair meets in, the roster, and
the hello each end of a link signs.

  * both ends work out the same room; every pair and every mesh gets its own;
  * the roster keeps only admissions an owner signed for this mesh, and two
    devices converge by trading it;
  * a link stands only between members, each proving it is the device the link
    is for, over this very connection (the fingerprints cross-match).
"""
from __future__ import annotations

import re

import pytest
from secdogie_identity import Identity
from secdogie_transport.meshlink import Roster, create_hello, link_room, verify_hello
from secdogie_transport.pairing import create_admission

OWNER, PHONE, TABLET, STRANGER, OTHER_OWNER = (Identity.generate() for _ in range(5))
T0 = 1_800_000_000
FP_A, FP_B, FP_M = "aa" * 32, "bb" * 32, "ee" * 32
N1, N2 = "01" * 16, "02" * 16


def _admit(device, owner=OWNER, mesh="home", t=T0, name=""):
    return create_admission(owner, mesh, device.did, invite_id="0" * 32, name=name, clock=lambda: t)


def _roster(*admissions, owners=(OWNER.did,)):
    r = Roster("home", owners)
    for a in admissions:
        r.admit(a)
    return r


def test_both_ends_meet_in_the_same_room_and_every_pair_has_its_own():
    room = link_room("home", OWNER.did, PHONE.did)
    assert room == link_room("home", PHONE.did, OWNER.did)
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", room)  # what the signaling gateway accepts
    assert room != link_room("home", OWNER.did, TABLET.did) != link_room("office", OWNER.did, PHONE.did)
    with pytest.raises(ValueError):
        link_room("home", PHONE.did, PHONE.did)


def test_the_roster_keeps_only_what_an_owner_signed_for_this_mesh():
    r = _roster()
    assert r.admit(_admit(PHONE, name="我的手机"))
    assert not r.admit(_admit(TABLET, owner=OTHER_OWNER))  # not our owner
    assert not r.admit(_admit(TABLET, mesh="office"))  # not our mesh
    assert not r.admit({**_admit(TABLET), "name": "x"})  # altered
    assert not r.admit(_admit(PHONE, t=T0 - 5))  # older than the one held
    assert r.devices() == sorted([OWNER.did, PHONE.did])
    assert r.is_member(OWNER.did) and r.is_member(PHONE.did) and not r.is_member(TABLET.did)
    assert r.name(PHONE.did) == "我的手机"


def test_two_devices_converge_by_trading_rosters():
    at_owner = _roster(_admit(PHONE), _admit(TABLET))
    at_phone = _roster(_admit(PHONE))  # a freshly paired phone knows only itself and the owner
    assert at_owner.digest() != at_phone.digest()
    assert at_phone.merge(at_owner.wire()) == 1
    assert at_phone.digest() == at_owner.digest() and at_phone.devices() == at_owner.devices()
    assert at_phone.merge(at_owner.wire()) == 0
    # a roster from another mesh, or one carrying a stranger's admissions, adds nothing
    assert at_phone.merge({**at_owner.wire(), "mesh": "office"}) == 0
    assert at_phone.merge({"mesh": "home", "admissions": [_admit(STRANGER, owner=OTHER_OWNER)]}) == 0
    assert "owners" not in at_owner.wire()  # owners never travel: each device trusts its own


def _hello(identity, *, local=FP_A, remote=FP_B, admission=None, t=T0, mesh="home"):
    return create_hello(identity, mesh=mesh, nonce=N1, local_fp=local, remote_fp=remote, admission=admission,
                        name="", roster="", clock=lambda: t)


def test_a_link_stands_between_members_over_this_very_connection():
    phone_adm = _admit(PHONE)
    at_owner, at_phone = _roster(phone_adm), _roster(phone_adm)
    from_phone = _hello(PHONE, local=FP_B, remote=FP_A, admission=phone_adm)
    from_owner = _hello(OWNER, local=FP_A, remote=FP_B)
    assert verify_hello(from_phone, roster=at_owner, expect_did=PHONE.did, my_local_fp=FP_A, my_remote_fp=FP_B,
                        now=T0).ok
    assert verify_hello(from_owner, roster=at_phone, expect_did=OWNER.did, my_local_fp=FP_B, my_remote_fp=FP_A,
                        now=T0).ok


def test_a_device_the_roster_does_not_know_yet_proves_itself_with_its_admission():
    tablet_adm = _admit(TABLET)
    at_phone = _roster(_admit(PHONE))  # has not heard of the tablet
    hello = _hello(TABLET, local=FP_B, remote=FP_A, admission=tablet_adm)
    assert verify_hello(hello, roster=at_phone, expect_did=TABLET.did, my_local_fp=FP_A, my_remote_fp=FP_B,
                        now=T0).ok
    bare = _hello(TABLET, local=FP_B, remote=FP_A)
    assert verify_hello(bare, roster=at_phone, expect_did=TABLET.did, my_local_fp=FP_A, my_remote_fp=FP_B,
                        now=T0).reason == "not a device of this mesh"


@pytest.mark.parametrize("hello, expect, mine, reason", [
    # a stranger with an admission from another owner
    (lambda: _hello(STRANGER, local=FP_B, remote=FP_A, admission=_admit(STRANGER, owner=OTHER_OWNER)),
     lambda: STRANGER.did, (FP_A, FP_B), "not a device of this mesh"),
    # a member, but not the one this link (room) is for
    (lambda: _hello(PHONE, local=FP_B, remote=FP_A), lambda: TABLET.did, (FP_A, FP_B),
     "this is not the device this link is for"),
    # someone between the two devices: each sees the middleman's certificate
    (lambda: _hello(PHONE, local=FP_B, remote=FP_M), lambda: PHONE.did, (FP_A, FP_M),
     "the fingerprints do not match: someone is between the two devices"),
    (lambda: _hello(PHONE, local=FP_B, remote=FP_A, t=T0 - 500), lambda: PHONE.did, (FP_A, FP_B),
     "the hello is too old or from the future"),
    (lambda: _hello(PHONE, local=FP_B, remote=FP_A, mesh="office"), lambda: PHONE.did, (FP_A, FP_B),
     "the hello is for another mesh"),
    (lambda: {**_hello(PHONE, local=FP_B, remote=FP_A), "name": "x"}, lambda: PHONE.did, (FP_A, FP_B),
     "the signature is not the sender's"),
    (lambda: "junk", lambda: PHONE.did, (FP_A, FP_B), "not a hello"),
])
def test_a_hello_that_does_not_hold_fails_for_its_reason(hello, expect, mine, reason):
    roster = _roster(_admit(PHONE))
    checked = verify_hello(hello(), roster=roster, expect_did=expect(), my_local_fp=mine[0], my_remote_fp=mine[1],
                           now=T0)
    assert not checked.ok and checked.reason == reason


def test_inputs_are_strict():
    with pytest.raises(ValueError):
        create_hello(PHONE, mesh="home", nonce="x", local_fp=FP_A, remote_fp=FP_B)
    with pytest.raises(ValueError):
        create_hello(PHONE, mesh="home", nonce=N2, local_fp="AB:CD", remote_fp=FP_B)
    with pytest.raises(ValueError):
        create_hello(PHONE, mesh="home", nonce=N2, local_fp=FP_A, remote_fp=FP_B, admission={"type": "x"})
    with pytest.raises(ValueError):
        Roster("home", ())
