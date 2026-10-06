/**
 * Gate 2, operator side: decide whether a node's challenge may be signed --
 * `dialogue/secdogie_dialogue/guard.py`, same checks and same token.
 *
 * The challenge is checked on its own terms, without trusting the node:
 *  - `action_hash` is recomputed here from the action *shown* and must equal
 *    the node's claim, so a node cannot show "delete tmp.txt" and collect a
 *    signature for something else;
 *  - the subject must be the node this session is authenticated with, so a
 *    node cannot relay another node's challenge to borrow the operator;
 *  - it must not have expired.
 * Only then is a token signed -- over the action shown, bound to the session
 * peer, valid no longer than the challenge.
 */

import type { Signer } from '../core/ed25519.ts';
import { actionHash, createAuthorization } from './authz.ts';
import { type Gate2ChallengePacket, type Gate2ResponsePacket, Verdict } from './wire.ts';

export const DEFAULT_TTL_SECONDS = 120;
/** Matches the wire's skew window: valid_from is backdated by this much. */
export const CLOCK_LEEWAY_SECONDS = 30;

export class GuardRefusal extends Error {}

export interface ChallengeReview {
  readonly challenge: Gate2ChallengePacket;
  /** Recomputed here from the action shown to the operator. */
  readonly localHash: string;
  readonly problems: readonly string[];
  readonly hashMatches: boolean;
  readonly signable: boolean;
}

export async function reviewChallenge(
  challenge: Gate2ChallengePacket,
  opts: { peerDid: string; now: number },
): Promise<ChallengeReview> {
  const localHash = await actionHash(challenge.target_action);
  const problems: string[] = [];
  if (localHash !== challenge.action_hash) {
    problems.push("the node's action_hash does not match the action shown (recomputed locally)");
  }
  if (challenge.subject_did !== opts.peerDid) {
    problems.push('the challenge is for a different node than the one in this session');
  }
  if (opts.now >= challenge.expires_at) problems.push('the challenge has expired');
  return { challenge, localHash, problems, hashMatches: localHash === challenge.action_hash, signable: problems.length === 0 };
}

/**
 * The operator's answer. Deny always succeeds and carries no token; Approve
 * signs with `operator` only if the review finds nothing wrong, else throws
 * {@link GuardRefusal} and nothing is signed.
 */
export async function respond(
  challenge: Gate2ChallengePacket,
  verdict: Verdict,
  opts: { peerDid: string; operator?: Signer; now: number; ttlSeconds?: number },
): Promise<Gate2ResponsePacket> {
  const review = await reviewChallenge(challenge, { peerDid: opts.peerDid, now: opts.now });
  if (verdict === Verdict.Deny) {
    return { challenge_id: challenge.challenge_id, action_hash: review.localHash, user_verdict: Verdict.Deny, authorization: {} };
  }
  if (verdict !== Verdict.Approve) throw new Error(`unknown verdict ${String(verdict)}`);
  if (!review.signable) throw new GuardRefusal(review.problems.join('; '));
  if (opts.operator === undefined) throw new GuardRefusal('no operator key unlocked');
  const token = await createAuthorization(opts.operator, challenge.target_action, opts.peerDid, {
    validFrom: opts.now - CLOCK_LEEWAY_SECONDS,
    expiresAt: Math.min(opts.now + (opts.ttlSeconds ?? DEFAULT_TTL_SECONDS), challenge.expires_at),
  });
  return { challenge_id: challenge.challenge_id, action_hash: review.localHash, user_verdict: Verdict.Approve, authorization: token };
}
