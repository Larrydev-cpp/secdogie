"""Pairing one of your own devices: the invite, the bind that ties the WebRTC
channel to the two keys, the check code both screens show, and the admission.

  * a whole pairing between two keys ends in an admission any node verifies;
  * someone in the middle of the signaling is caught by the fingerprints, and
    their check code would differ anyway;
  * an expired, altered or re-addressed statement fails, each for its reason.
"""
from __future__ import annotations

import pytest
from secdogie_identity import Allowlist, Identity
from secdogie_transport.endpoint import Endpoint
from secdogie_transport.membership import sign_record
from secdogie_transport.pairing import (
    DEVICE,
    INVITER,
    check_code,
    create_admission,
    create_bind,
    create_invite,
    decode_invite,
    encode_invite,
    normalize_fingerprint,
    own_devices,
    verify_admission,
    verify_bind,
    verify_invite,
)
from secdogie_transport.peerbook import OWN, PeerBook

OWNER, PHONE, STRANGER = (Identity.generate() for _ in range(3))
T0 = 1_800_000_000
FP_A = "sha-256 " + ":".join(["AB"] * 32)  # as SDP writes it
FP_B = "cd" * 32
FP_M = "ee" * 32  # a middleman's certificate
N1, N2 = "01" * 16, "02" * 16


def _invite(**kw):
    args = {"room": "r-" + "7" * 20, "signal": "wss://signal.example/ws", "clock": lambda: T0, **kw}
    return create_invite(OWNER, "home", **args)


def test_a_whole_pairing_ends_in_an_admission_any_node_verifies():
    link = encode_invite(_invite())
    invite = verify_invite(decode_invite(link), now=T0 + 5)
    assert invite.ok and invite.value["mesh"] == "home" and invite.value["inviter"] == OWNER.did
    inv = invite.value
    # the channel is open: each side signs what it sees of it
    theirs = create_bind(PHONE, invite_id=inv["invite_id"], role=DEVICE, nonce=N2, local_fp=FP_B, remote_fp=FP_A,
                         name="我的手机", clock=lambda: T0 + 6)
    mine = create_bind(OWNER, invite_id=inv["invite_id"], role=INVITER, nonce=N1, local_fp=FP_A, remote_fp=FP_B,
                       clock=lambda: T0 + 6)
    assert verify_bind(theirs, invite=inv, expect_role=DEVICE, my_local_fp=FP_A, my_remote_fp=FP_B, now=T0 + 7).ok
    assert verify_bind(mine, invite=inv, expect_role=INVITER, my_local_fp=FP_B, my_remote_fp=FP_A, now=T0 + 7).ok
    # both screens show the same six digits
    code = check_code(inv["invite_id"], mine, theirs)
    assert len(code) == 6 and code.isdigit()
    # the person confirms; the owner admits the device
    admission = create_admission(OWNER, "home", PHONE.did, invite_id=inv["invite_id"], name="我的手机",
                                 clock=lambda: T0 + 20)
    checked = verify_admission(admission, owners=Allowlist({OWNER.did}), mesh="home", device=PHONE.did)
    assert checked.ok and checked.value["roles"] == ["forward"] and checked.value["name"] == "我的手机"


def test_someone_in_the_middle_of_the_signaling_is_caught():
    inv = _invite()
    # the middleman terminates DTLS on both sides: each device sees FP_M as the other's certificate
    from_phone = create_bind(PHONE, invite_id=inv["invite_id"], role=DEVICE, nonce=N2, local_fp=FP_B, remote_fp=FP_M,
                             clock=lambda: T0)
    seen = verify_bind(from_phone, invite=inv, expect_role=DEVICE, my_local_fp=FP_A, my_remote_fp=FP_M, now=T0)
    assert not seen.ok and "someone is between" in seen.reason
    # and the codes the two honest screens would show differ
    honest = create_bind(OWNER, invite_id=inv["invite_id"], role=INVITER, nonce=N1, local_fp=FP_A, remote_fp=FP_M,
                         clock=lambda: T0)
    relayed = create_bind(STRANGER, invite_id=inv["invite_id"], role=INVITER, nonce=N1, local_fp=FP_M,
                          remote_fp=FP_B, clock=lambda: T0)
    assert check_code(inv["invite_id"], honest, from_phone) != check_code(inv["invite_id"], relayed, from_phone)


@pytest.mark.parametrize("change, reason", [
    (lambda i: {**i, "mesh": "office"}, "the signature is not the inviter's"),
    (lambda i: {**i, "type": "secdogie/device-admission/v1"}, "not an invite"),
    (lambda i: "junk", "not an invite"),
])
def test_an_altered_invite_fails(change, reason):
    assert verify_invite(change(_invite()), now=T0 + 1).reason == reason


def test_invites_expire_and_binds_must_be_for_this_invite_this_role_and_this_moment():
    inv = _invite(ttl=60)
    assert verify_invite(inv, now=T0 + 61).reason == "the invite has expired"
    assert decode_invite("%%%") is None
    bind = create_bind(PHONE, invite_id=inv["invite_id"], role=DEVICE, nonce=N2, local_fp=FP_B, remote_fp=FP_A,
                       clock=lambda: T0)
    see = {"my_local_fp": FP_A, "my_remote_fp": FP_B}
    assert verify_bind(bind, invite=_invite(room="another-room"), expect_role=DEVICE, now=T0, **see).reason == \
        "the bind is for another invite"
    assert verify_bind(bind, invite=inv, expect_role=INVITER, now=T0, **see).reason == "the wrong side sent this bind"
    assert verify_bind(bind, invite=inv, expect_role=DEVICE, now=T0 + 500, **see).reason == \
        "the bind is too old or from the future"
    assert verify_bind({**bind, "name": "x"}, invite=inv, expect_role=DEVICE, now=T0, **see).reason == \
        "the signature is not the sender's"
    # only the inviter named in the invite can be the inviting side
    fake = create_bind(STRANGER, invite_id=inv["invite_id"], role=INVITER, nonce=N1, local_fp=FP_A, remote_fp=FP_B,
                       clock=lambda: T0)
    assert verify_bind(fake, invite=inv, expect_role=INVITER, my_local_fp=FP_B, my_remote_fp=FP_A, now=T0).reason == \
        "the other side is not the inviter"


def test_only_an_owner_admits_and_only_for_its_mesh_and_device():
    inv = _invite()
    good = create_admission(OWNER, "home", PHONE.did, invite_id=inv["invite_id"], clock=lambda: T0)
    owners = Allowlist({OWNER.did})
    assert verify_admission(good, owners=owners, mesh="office").reason == "the admission is for another mesh"
    assert verify_admission(good, owners=owners, device=STRANGER.did).reason == "the admission is for another device"
    assert verify_admission(good, owners=Allowlist({STRANGER.did})).reason == "not signed by an owner of this mesh"
    assert verify_admission(good, owners=None).reason == "no owners configured"
    assert verify_admission({**good, "roles": ["forward", "admin"]}, owners=owners).reason == \
        "invalid or missing signature"
    with pytest.raises(ValueError):
        create_admission(OWNER, "home", OWNER.did, invite_id=inv["invite_id"])
    with pytest.raises(ValueError):
        create_admission(OWNER, "home", PHONE.did, invite_id=inv["invite_id"], roles=("admin",))


def test_admissions_decide_which_nodes_are_your_own():
    inv = _invite()
    phone = create_admission(OWNER, "home", PHONE.did, invite_id=inv["invite_id"], clock=lambda: T0)
    elsewhere = create_admission(OWNER, "office", STRANGER.did, invite_id=inv["invite_id"], clock=lambda: T0)
    forged = create_admission(STRANGER, "home", OWNER.did, invite_id=inv["invite_id"], clock=lambda: T0)
    found = own_devices([phone, elsewhere, forged, "junk", {**phone, "name": "x"}], owners=[OWNER.did], mesh="home")
    assert found == {PHONE.did}
    assert own_devices([phone], owners=Allowlist({OWNER.did}), mesh="home") == {PHONE.did}
    assert own_devices([phone], owners=None, mesh="home") == frozenset()
    # and the address book then keeps the phone as one of your own nodes
    book = PeerBook(OWNER.did, own=found)
    assert book.admit(sign_record(PHONE, [Endpoint("local", "192.168.1.20", 7400)], last_seen=1.0)) == OWN
    assert book.admit(sign_record(STRANGER, [Endpoint("public", "203.0.113.9", 7400)], last_seen=1.0)) is None


def test_fingerprints_and_inputs_are_strict():
    assert normalize_fingerprint(FP_A) == "ab" * 32 and normalize_fingerprint("AB:CD") == ""
    assert normalize_fingerprint(None) == ""
    with pytest.raises(ValueError):
        _invite(signal="https://not-a-websocket.example/")
    with pytest.raises(ValueError):
        _invite(room="has space")
    with pytest.raises(ValueError):
        _invite(ttl=99999)
    with pytest.raises(ValueError):
        create_bind(PHONE, invite_id="x", role=DEVICE, nonce=N2, local_fp=FP_B, remote_fp=FP_A)
