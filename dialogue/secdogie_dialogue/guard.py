"""Gate 2, operator side: turn a node's challenge into a signed approval -- or
refuse to.

When the node's action-plan gate rejects a destructive action for want of an
operator signature (``UNAUTHORIZED_ACTION``), the node sends a
``Gate2ChallengePacket``: the action, a risk statement, the hash it claims for
the action, and the node DID the approval would be for. Before anything can be
signed, the App checks the challenge **itself**, without trusting the node:

  * it recomputes ``action_hash`` locally from the ``target_action`` it shows the
    operator (the very function the node verifies with) and requires it to equal
    the node's claim -- so a node cannot show "delete tmp.txt" and ask for a
    signature over something else;
  * the approval's subject must be the node this session is authenticated with
    -- so a node cannot relay another node's challenge to borrow the operator;
  * the challenge must not have expired.

Only then may the operator Approve, and the token signed is
``authz.create_authorization`` over the locally shown action, bound to the
session peer's DID, and valid no longer than the challenge. A Deny never carries
a token. The node still verifies the token in full (``verify_authorization``);
this side only decides whether to sign.

Pure given a clock; the operator key comes in as an ``Identity`` (see
``keystore.unseal_identity``) and is never stored here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from secdogie_citadel.authz import action_hash, create_authorization

from .protocol import Gate2ChallengePacket, Gate2ResponsePacket, Verdict

DEFAULT_TTL = 120.0  # an approval is short-lived; never longer than its challenge
CLOCK_LEEWAY = 30.0  # matches the protocol's skew window: valid_from is backdated by this


class GuardRefusal(Exception):
    """The App will not sign this challenge."""


@dataclass(frozen=True)
class ChallengeReview:
    challenge: Gate2ChallengePacket
    local_hash: str  # recomputed here from the action shown to the operator
    problems: tuple[str, ...]

    @property
    def hash_matches(self) -> bool:
        return self.local_hash == self.challenge.action_hash

    @property
    def signable(self) -> bool:
        return not self.problems


def review_challenge(challenge: Gate2ChallengePacket, *, peer_did: str, now: float | None = None,
                     clock=time.time) -> ChallengeReview:
    """Check a challenge before the operator is offered Approve. ``peer_did`` is
    the DID of the node this session is authenticated with (the envelope's
    signer), not anything the challenge says about itself."""
    t = float(now) if now is not None else float(clock())
    local = action_hash(challenge.target_action)
    problems = []
    if local != challenge.action_hash:
        problems.append("the node's action_hash does not match the action shown (recomputed locally)")
    if challenge.subject_did != peer_did:
        problems.append("the challenge is for a different node than the one in this session")
    if t >= challenge.expires_at:
        problems.append("the challenge has expired")
    return ChallengeReview(challenge, local, tuple(problems))


def respond(challenge: Gate2ChallengePacket, verdict: Verdict, *, peer_did: str, operator=None,
            now: float | None = None, clock=time.time, ttl: float = DEFAULT_TTL) -> Gate2ResponsePacket:
    """The operator's answer to ``challenge``.

    ``Verdict.DENY`` always succeeds and carries no token. ``Verdict.APPROVE``
    signs with ``operator`` only if ``review_challenge`` finds nothing wrong;
    otherwise it raises ``GuardRefusal`` and nothing is signed."""
    t = float(now) if now is not None else float(clock())
    review = review_challenge(challenge, peer_did=peer_did, now=t)
    if verdict is Verdict.DENY:
        return Gate2ResponsePacket(challenge.challenge_id, review.local_hash, Verdict.DENY)
    if verdict is not Verdict.APPROVE:
        raise ValueError(f"unknown verdict {verdict!r}")
    if not review.signable:
        raise GuardRefusal("; ".join(review.problems))
    if operator is None:
        raise GuardRefusal("no operator key unlocked")
    token = create_authorization(
        operator,
        challenge.target_action,  # the action shown, hashed here -- not the node's claimed hash
        peer_did,
        valid_from=t - CLOCK_LEEWAY,
        expires_at=min(t + float(ttl), challenge.expires_at),
    )
    return Gate2ResponsePacket(challenge.challenge_id, review.local_hash, Verdict.APPROVE, token)


__all__ = ["DEFAULT_TTL", "CLOCK_LEEWAY", "GuardRefusal", "ChallengeReview", "review_challenge", "respond"]
