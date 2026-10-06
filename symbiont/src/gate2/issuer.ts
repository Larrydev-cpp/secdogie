/**
 * Gate 2, agent side: the hand does not move on its own say-so.
 *
 * After Gate 1 has aligned an intent and the WASM engine has computed the
 * action deterministically, a high-risk action is held behind a challenge
 * (`Gate2ChallengePacket`, the v1 action field set, signed by the agent's DID)
 * and released only by a valid operator token: right action hash, this agent
 * as subject, inside its window, signed by a trusted operator -- the checks
 * of `verify_authorization`. A Deny, a mismatch, a bad token or silence until
 * the deadline all refuse; there is no path that releases without a token.
 *
 * Mirrors the challenge flow of `dialogue/secdogie_dialogue/agent_bridge.py`.
 */

import type { CanonObject } from '../core/canon.ts';
import type { Signer } from '../core/ed25519.ts';
import type { TrustSet } from '../core/envelope.ts';
import { type TargetAction, actionHash, verifyAuthorization } from './authz.ts';
import { type Gate2ChallengePacket, type Gate2ResponsePacket, RiskLevel, Verdict, randomHex } from './wire.ts';

export const DEFAULT_CHALLENGE_TTL_SECONDS = 120;

/** What the WASM planner computed (`graph/src/plan.rs`). */
export interface PlanPreview {
  readonly target_action: TargetAction;
  readonly risk: 'low' | 'high' | 'irreversible';
  readonly mutating: boolean;
  readonly method: 'get' | 'post';
  readonly fields: readonly string[];
  readonly origin: string;
  readonly route: string;
}

export interface Release {
  readonly action: TargetAction;
  readonly challengeId: string | null;
  /** The operator token that released it; null for a low-risk action. */
  readonly token: CanonObject | null;
  readonly operator: string | null;
  readonly releasedAt: number;
}

export type IssueResult =
  | { readonly kind: 'released'; readonly release: Release }
  | { readonly kind: 'challenge'; readonly packet: Gate2ChallengePacket };

export type Settlement =
  | { readonly kind: 'released'; readonly release: Release }
  | { readonly kind: 'refused'; readonly challengeId: string; readonly reason: string };

export class Gate2Issuer {
  readonly agent: Signer;
  readonly #operators: TrustSet;
  readonly #ttl: number;
  readonly #clock: () => number;
  readonly #newId: () => string;
  readonly #pending = new Map<string, { action: TargetAction; hash: string; expiresAt: number }>();

  constructor(opts: {
    agent: Signer;
    operators: TrustSet;
    ttlSeconds?: number;
    clock?: () => number;
    idFactory?: () => string;
  }) {
    this.agent = opts.agent;
    this.#operators = opts.operators;
    this.#ttl = opts.ttlSeconds ?? DEFAULT_CHALLENGE_TTL_SECONDS;
    this.#clock = opts.clock ?? (() => Date.now() / 1000);
    this.#newId = opts.idFactory ?? (() => randomHex(8));
  }

  /** A low-risk action passes; a high-risk one gets a challenge. */
  async gate(preview: PlanPreview, explanation: string): Promise<IssueResult> {
    const action = preview.target_action;
    if (!action.high_risk && preview.risk === 'low') {
      return {
        kind: 'released',
        release: { action, challengeId: null, token: null, operator: null, releasedAt: this.#clock() },
      };
    }
    const hash = await actionHash(action);
    const expiresAt = this.#clock() + this.#ttl;
    const packet: Gate2ChallengePacket = {
      challenge_id: this.#newId(),
      target_action: action,
      risk_level: preview.risk === 'irreversible' ? RiskLevel.Irreversible : RiskLevel.High,
      risk_explanation: explanation,
      action_hash: hash,
      subject_did: this.agent.did,
      expires_at: expiresAt,
    };
    this.#pending.set(packet.challenge_id, { action, hash, expiresAt });
    return { kind: 'challenge', packet };
  }

  /** Settles a challenge with the operator's response. One response per challenge. */
  async receive(resp: Gate2ResponsePacket, now = this.#clock()): Promise<Settlement> {
    const id = resp.challenge_id;
    const p = this.#pending.get(id);
    const refuse = (reason: string): Settlement => ({ kind: 'refused', challengeId: id, reason });
    if (p === undefined) return refuse('no pending challenge with that id');
    this.#pending.delete(id);
    if (now >= p.expiresAt) return refuse('the challenge expired before the answer arrived');
    if (resp.user_verdict !== Verdict.Approve) return refuse('the operator denied it');
    if (resp.action_hash !== p.hash) return refuse('the answer is for a different action');
    const res = await verifyAuthorization(resp.authorization, p.action, {
      operators: this.#operators,
      subject: this.agent.did,
      now,
    });
    if (!res.ok) return refuse(`authorization rejected: ${res.reason}`);
    return {
      kind: 'released',
      release: { action: p.action, challengeId: id, token: resp.authorization, operator: res.signer, releasedAt: now },
    };
  }

  /** Expires unanswered challenges (each is a refusal); returns their ids. */
  expire(now = this.#clock()): string[] {
    const gone: string[] = [];
    for (const [id, p] of this.#pending) {
      if (now >= p.expiresAt) {
        this.#pending.delete(id);
        gone.push(id);
      }
    }
    return gone;
  }

  get pendingIds(): readonly string[] {
    return [...this.#pending.keys()];
  }
}
