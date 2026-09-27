"""Gate 2, operator side. The App signs only a challenge it has checked itself
(hash recomputed locally, subject = the session peer, not expired); the token it
signs is exactly what the node's gate verifies. End to end: the node's gate
rejects a destructive action, the App approves the challenge over the wire, and
the gate then allows exactly that action and nothing else."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.action_gate import ALLOW, REJECT, UNAUTHORIZED_ACTION, GateContext, PlannedAction, gate
from secdogie_citadel.authz import action_hash, verify_authorization
from secdogie_dialogue.guard import CLOCK_LEEWAY, GuardRefusal, respond, review_challenge
from secdogie_dialogue.protocol import (
    Gate2ChallengePacket,
    Gate2ResponsePacket,
    ReplayGuard,
    RiskLevel,
    Sender,
    TargetAction,
    Verdict,
    open_envelope,
)
from secdogie_identity import Allowlist, Identity, MasterSet, TrustPolicy, cosign, create_revocation

NOW = 1_000_000.0
NODE = Identity.generate()  # the Agent node
DEVICE = Identity.generate()  # the App's session key: signs envelopes
OPERATOR = Identity.generate()  # the operator's Gate 2 key: signs authorizations only

DELETE = PlannedAction(kind="delete", target_id="file-42", target_role="button", target_name="Delete",
                       expected_observation="the file is gone")


def _challenge(action=DELETE, *, claimed_hash=None, subject=None, expires_at=NOW + 60):
    return Gate2ChallengePacket(
        challenge_id="ch-1",
        target_action=TargetAction.from_action(action),
        risk_level=RiskLevel.IRREVERSIBLE,
        risk_explanation="deletes a file; there is no undo",
        action_hash=claimed_hash if claimed_hash is not None else action_hash(action),
        subject_did=subject or NODE.did,
        expires_at=expires_at,
    )


def _gate_ctx(token, operators=None):
    return GateContext(require_authorization=True, authorization=token,
                       # `is None`, not `or`: a TrustPolicy with every DID revoked is empty, hence falsy
                       operators=operators if operators is not None else Allowlist({OPERATOR.did}),
                       subject_did=NODE.did, now=NOW + 1, requires_verification=False)


# ---- review ---------------------------------------------------------------------


def test_a_well_formed_challenge_is_signable():
    r = review_challenge(_challenge(), peer_did=NODE.did, now=NOW)
    assert r.signable and r.hash_matches and r.local_hash == action_hash(DELETE)


def test_a_lying_hash_is_caught_locally():
    # The node shows "delete file-42" but claims the hash of deleting /etc.
    lie = _challenge(claimed_hash=action_hash(PlannedAction(kind="delete", target_id="/etc")))
    r = review_challenge(lie, peer_did=NODE.did, now=NOW)
    assert not r.signable and not r.hash_matches
    with pytest.raises(GuardRefusal, match="does not match"):
        respond(lie, Verdict.APPROVE, peer_did=NODE.did, operator=OPERATOR, now=NOW)


def test_a_challenge_for_another_node_is_not_signed():
    other = Identity.generate()
    relayed = _challenge(subject=other.did)
    with pytest.raises(GuardRefusal, match="different node"):
        respond(relayed, Verdict.APPROVE, peer_did=NODE.did, operator=OPERATOR, now=NOW)


def test_an_expired_challenge_is_not_signed():
    with pytest.raises(GuardRefusal, match="expired"):
        respond(_challenge(expires_at=NOW), Verdict.APPROVE, peer_did=NODE.did, operator=OPERATOR, now=NOW)


def test_approve_without_an_unlocked_key_signs_nothing():
    with pytest.raises(GuardRefusal, match="no operator key"):
        respond(_challenge(), Verdict.APPROVE, peer_did=NODE.did, operator=None, now=NOW)


# ---- the response ---------------------------------------------------------------


def test_deny_never_carries_a_token_even_for_a_bad_challenge():
    for ch in (_challenge(), _challenge(claimed_hash="00" * 32)):
        resp = respond(ch, Verdict.DENY, peer_did=NODE.did, operator=OPERATOR, now=NOW)
        assert resp.user_verdict is Verdict.DENY and resp.authorization == {}


def test_the_token_is_bound_to_the_shown_action_the_session_peer_and_the_challenge_window():
    ch = _challenge(expires_at=NOW + 60)
    resp = respond(ch, Verdict.APPROVE, peer_did=NODE.did, operator=OPERATOR, now=NOW, ttl=600)
    tok = resp.authorization
    assert tok["signer"] == OPERATOR.did
    assert tok["subject"] == NODE.did
    assert tok["action_hash"] == action_hash(DELETE) == resp.action_hash
    assert tok["expires_at"] == ch.expires_at  # capped at the challenge, not now + ttl
    assert tok["valid_from"] == NOW - CLOCK_LEEWAY
    short = respond(ch, Verdict.APPROVE, peer_did=NODE.did, operator=OPERATOR, now=NOW, ttl=10)
    assert short.authorization["expires_at"] == NOW + 10


def test_the_token_authorizes_that_action_and_no_other():
    tok = respond(_challenge(), Verdict.APPROVE, peer_did=NODE.did, operator=OPERATOR, now=NOW).authorization
    ops = Allowlist({OPERATOR.did})
    assert verify_authorization(tok, DELETE, operators=ops, subject=NODE.did, now=NOW + 1).ok
    other = PlannedAction(kind="delete", target_id="/etc")
    assert not verify_authorization(tok, other, operators=ops, subject=NODE.did, now=NOW + 1).ok


# ---- end to end over the wire ---------------------------------------------------


def _wire(sender_id, recipient_id, trust, packet, replay):
    obj = Sender(sender_id, recipient_id.did, clock_ns=lambda: int(NOW * 1e9)).seal(packet)
    return open_envelope(obj, trust=trust, self_did=recipient_id.did, replay=replay)


def test_end_to_end_reject_challenge_approve_allow():
    node_replay = ReplayGuard(clock_ns=lambda: int(NOW * 1e9))
    app_replay = ReplayGuard(clock_ns=lambda: int(NOW * 1e9))
    node_sessions = Allowlist({DEVICE.did})  # who may talk to the node
    node_operators = Allowlist({OPERATOR.did})  # who may authorize on it
    app_trust = Allowlist({NODE.did})

    # 1. the node's gate refuses the destructive action without a token
    first = gate(DELETE, _gate_ctx(None, node_operators))
    assert first.verdict == REJECT and UNAUTHORIZED_ACTION in first.findings

    # 2. node -> App: the challenge
    opened = _wire(NODE, DEVICE, app_trust, _challenge(), app_replay)
    assert opened.ok, opened.reason

    # 3. App reviews against the authenticated peer and the operator approves
    resp = respond(opened.envelope.packet, Verdict.APPROVE, peer_did=opened.envelope.signer,
                   operator=OPERATOR, now=NOW)

    # 4. App -> node: the response, over the session key
    back = _wire(DEVICE, NODE, node_sessions, resp, node_replay)
    assert back.ok, back.reason
    assert isinstance(back.envelope.packet, Gate2ResponsePacket)

    # 5. the gate now allows exactly this action
    token = back.envelope.packet.authorization
    assert gate(DELETE, _gate_ctx(token, node_operators)).verdict == ALLOW
    swapped = PlannedAction(kind="delete", target_id="/etc", expected_observation="gone")
    assert gate(swapped, _gate_ctx(token, node_operators)).verdict == REJECT


def test_the_session_key_alone_cannot_authorize():
    # A stolen device key can sign envelopes, but a token it signs is not an
    # operator's: the gate refuses it.
    forged = respond(_challenge(), Verdict.APPROVE, peer_did=NODE.did, operator=DEVICE, now=NOW)
    assert gate(DELETE, _gate_ctx(forged.authorization)).verdict == REJECT


def test_a_revoked_operators_approval_stops_counting():
    master = Identity.generate()
    policy = TrustPolicy(Allowlist({OPERATOR.did}), masters=MasterSet([master.did]))
    token = respond(_challenge(), Verdict.APPROVE, peer_did=NODE.did, operator=OPERATOR, now=NOW).authorization
    assert gate(DELETE, _gate_ctx(token, policy)).verdict == ALLOW
    policy.apply(cosign(master, create_revocation([OPERATOR.did])))
    assert gate(DELETE, _gate_ctx(token, policy)).verdict == REJECT
