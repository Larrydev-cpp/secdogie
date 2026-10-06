/**
 * Gate 2, operator side, as inline chat bubbles -- never a modal.
 *
 * A challenge the attention queue has surfaced becomes a {@link Gate2Bubble}
 * in the conversation. The bubble carries everything the signature commits to
 * (the six action fields, the risk, the hash recomputed here), and its Approve
 * is guarded the way a modal would never need to be, because nothing else
 * stops the page from moving under the pointer:
 *
 *  - **arming delay**: Approve does nothing until the bubble has been on
 *    screen for `armingDelaySeconds` (and again after being un-veiled), so a
 *    click aimed at something else cannot land on a bubble that just appeared;
 *  - **veiled while sensitive**: in a sensitive context the bubble's content is
 *    hidden and it cannot be approved;
 *  - **review twice**: on arrival and again at the moment of signing;
 *  - **the key needs the person**: {@link OperatorKeyring.unlock} is where a
 *    browser keyring demands a real user activation;
 *  - **self-check**: the token is verified locally before it is sent.
 *
 * Deny is always available and carries no token. An unanswered bubble expires,
 * and the node refuses the action.
 */

import type { Signer } from '../core/ed25519.ts';
import { TrustSet } from '../core/envelope.ts';
import { verifyAuthorization } from './authz.ts';
import { type ChallengeReview, GuardRefusal, respond, reviewChallenge } from './guard.ts';
import { type Gate2ChallengePacket, type Gate2ResponsePacket, Verdict } from './wire.ts';

export const ARMING_DELAY_SECONDS = 0.8;
const SETTLED_MEMORY = 1024;

export interface OperatorKeyring {
  readonly did: string;
  unlock(): Promise<Signer>;
}

/** Holds a signer in memory -- for tests and for surfaces that unlock elsewhere. */
export class MemoryKeyring implements OperatorKeyring {
  readonly #signer: Signer;
  constructor(signer: Signer) {
    this.#signer = signer;
  }
  get did(): string {
    return this.#signer.did;
  }
  async unlock(): Promise<Signer> {
    return this.#signer;
  }
}

/**
 * Releases the operator key only inside a real user activation (a click or a
 * key press the browser saw), so script on the page -- the agent's included --
 * cannot sign on the person's behalf. Fails closed where the browser cannot
 * tell.
 */
export class UserGestureKeyring implements OperatorKeyring {
  readonly #signer: Signer;
  constructor(signer: Signer) {
    this.#signer = signer;
  }
  get did(): string {
    return this.#signer.did;
  }
  async unlock(): Promise<Signer> {
    const ua = (globalThis as { navigator?: { userActivation?: { isActive: boolean } } }).navigator?.userActivation;
    if (!ua?.isActive) throw new GuardRefusal('the operator key opens only on your own click');
    return this.#signer;
  }
}

export type BubbleState = 'awaiting' | 'signing' | 'approved' | 'denied' | 'refused' | 'expired';

export interface Gate2Bubble {
  readonly id: string;
  readonly challenge: Gate2ChallengePacket;
  readonly review: ChallengeReview;
  readonly state: BubbleState;
  readonly surfacedAt: number;
  readonly armedAt: number;
  readonly veiled: boolean;
  readonly note: string;
}

export type ApproveResult =
  | { readonly ok: true; readonly response: Gate2ResponsePacket }
  | { readonly ok: false; readonly reason: string };

export class Gate2SignerFlow {
  readonly peerDid: string;
  readonly #keyring: OperatorKeyring;
  readonly #send: (r: Gate2ResponsePacket) => Promise<void> | void;
  readonly #clock: () => number;
  readonly #arming: number;
  readonly #bubbles = new Map<string, Gate2Bubble>();
  readonly #settled: string[] = [];
  onChange: ((b: Gate2Bubble) => void) | null = null;

  constructor(opts: {
    peerDid: string;
    keyring: OperatorKeyring;
    send: (r: Gate2ResponsePacket) => Promise<void> | void;
    clock?: () => number;
    armingDelaySeconds?: number;
  }) {
    this.peerDid = opts.peerDid;
    this.#keyring = opts.keyring;
    this.#send = opts.send;
    this.#clock = opts.clock ?? (() => Date.now() / 1000);
    this.#arming = opts.armingDelaySeconds ?? ARMING_DELAY_SECONDS;
  }

  #set(id: string, patch: Partial<Gate2Bubble>): Gate2Bubble {
    const b = { ...this.#bubbles.get(id)!, ...patch };
    this.#bubbles.set(id, b);
    if (b.state !== 'awaiting' && b.state !== 'signing') {
      this.#settled.push(id);
      if (this.#settled.length > SETTLED_MEMORY) this.#settled.shift();
    }
    this.onChange?.(b);
    return b;
  }

  /** Shows a surfaced challenge as an awaiting bubble. A repeat is ignored. */
  async present(challenge: Gate2ChallengePacket, now = this.#clock()): Promise<Gate2Bubble> {
    const existing = this.#bubbles.get(challenge.challenge_id);
    if (existing) return existing;
    if (this.#settled.includes(challenge.challenge_id)) throw new GuardRefusal('that challenge was already answered');
    const review = await reviewChallenge(challenge, { peerDid: this.peerDid, now });
    const b: Gate2Bubble = {
      id: challenge.challenge_id,
      challenge,
      review,
      state: review.signable ? 'awaiting' : 'refused',
      surfacedAt: now,
      armedAt: now + this.#arming,
      veiled: false,
      note: review.signable ? '' : review.problems.join('；'),
    };
    this.#bubbles.set(b.id, b);
    this.onChange?.(b);
    return b;
  }

  /** Hides a bubble's content (a sensitive context began). */
  veil(id: string): void {
    const b = this.#bubbles.get(id);
    if (b && !b.veiled) this.#set(id, { veiled: true });
  }

  /** Shows it again, re-armed: the person gets a fresh look before Approve works. */
  unveil(id: string, now = this.#clock()): void {
    const b = this.#bubbles.get(id);
    if (b?.veiled) this.#set(id, { veiled: false, armedAt: now + this.#arming });
  }

  async approve(id: string, now = this.#clock()): Promise<ApproveResult> {
    const b = this.#bubbles.get(id);
    if (!b) return { ok: false, reason: 'no such bubble' };
    if (b.state !== 'awaiting') return { ok: false, reason: `the bubble is ${b.state}` };
    if (b.veiled) return { ok: false, reason: 'hidden while the context is sensitive' };
    if (now < b.armedAt) return { ok: false, reason: 'not armed yet: give it a moment on screen' };
    this.#set(id, { state: 'signing' });
    const review = await reviewChallenge(b.challenge, { peerDid: this.peerDid, now });
    if (!review.signable) {
      this.#set(id, { state: 'refused', review, note: review.problems.join('；') });
      return { ok: false, reason: review.problems.join('; ') };
    }
    let operator: Signer;
    try {
      operator = await this.#keyring.unlock();
    } catch (e) {
      this.#set(id, { state: 'awaiting', note: (e as Error).message });
      return { ok: false, reason: (e as Error).message };
    }
    const response = await respond(b.challenge, Verdict.Approve, { peerDid: this.peerDid, operator, now });
    const self = await verifyAuthorization(response.authorization, b.challenge.target_action, {
      operators: new TrustSet([operator.did]),
      subject: this.peerDid,
      now,
    });
    if (!self.ok) {
      this.#set(id, { state: 'refused', note: `self-check failed: ${self.reason}` });
      return { ok: false, reason: `self-check failed: ${self.reason}` };
    }
    await this.#send(response);
    this.#set(id, { state: 'approved', review, note: '' });
    return { ok: true, response };
  }

  async deny(id: string, now = this.#clock()): Promise<ApproveResult> {
    const b = this.#bubbles.get(id);
    if (!b) return { ok: false, reason: 'no such bubble' };
    if (b.state !== 'awaiting' && b.state !== 'refused') return { ok: false, reason: `the bubble is ${b.state}` };
    const response = await respond(b.challenge, Verdict.Deny, { peerDid: this.peerDid, now });
    await this.#send(response);
    this.#set(id, { state: 'denied' });
    return { ok: true, response };
  }

  /** Bubbles past their challenge's deadline; the node refuses those actions. */
  expire(now = this.#clock()): Gate2Bubble[] {
    const out: Gate2Bubble[] = [];
    for (const b of this.#bubbles.values()) {
      if ((b.state === 'awaiting' || b.state === 'refused') && now >= b.challenge.expires_at) {
        out.push(this.#set(b.id, { state: 'expired', note: '节点会拒绝这个动作' }));
      }
    }
    return out;
  }

  bubble(id: string): Gate2Bubble | undefined {
    return this.#bubbles.get(id);
  }

  get bubbles(): readonly Gate2Bubble[] {
    return [...this.#bubbles.values()];
  }
}
