/**
 * Proposals waiting for the person -- a pure state machine.
 *
 *   queued ──(a gap)──▶ surfaced ──▶ resolved (approved | denied | answered | dismissed)
 *     │                    │
 *     └──────┬─────────────┘
 *            ├──(deadline)──▶ expired      fail closed: never an implicit yes
 *            └──(withdrawn)─▶ withdrawn
 *
 * Proposals are held while the budget has no slots and surfaced, a few at a
 * time, when a gap opens: time-bound first, then the earliest deadline, then
 * first come. In a suppressed context a surfaced proposal is *veiled* (its
 * content hidden) until the context ends. There is no urgent override: a Gate 2
 * challenge that expires while the person is in flow simply expires, and the
 * node refuses the action. Illegal transitions throw.
 */

import type { InterruptionBudget } from './budget.ts';

export type ProposalKind = 'gate1' | 'gate2';
export type ProposalState = 'queued' | 'surfaced' | 'resolved' | 'expired' | 'withdrawn';
export type ProposalResolution = 'approved' | 'denied' | 'answered' | 'dismissed';

export interface Proposal<P = unknown> {
  readonly id: string;
  readonly kind: ProposalKind;
  readonly payload: P;
  /** One natural line about what it is -- never a mechanical step. */
  readonly summary: string;
  readonly createdAt: number;
  /** Milliseconds; null for no deadline. */
  readonly expiresAt: number | null;
  readonly state: ProposalState;
  readonly resolution: ProposalResolution | null;
  readonly surfacedAt: number | null;
  readonly veiled: boolean;
}

export interface QueueEvents {
  readonly surfaced: readonly Proposal[];
  readonly expired: readonly Proposal[];
  readonly veiled: readonly Proposal[];
  readonly unveiled: readonly Proposal[];
}

const NEXT: Record<ProposalState, readonly ProposalState[]> = {
  queued: ['surfaced', 'expired', 'withdrawn'],
  surfaced: ['resolved', 'expired', 'withdrawn'],
  resolved: [],
  expired: [],
  withdrawn: [],
};

export class IllegalTransition extends Error {}

export class ProposalQueue {
  readonly maxSurfaced: number;
  readonly #items = new Map<string, Proposal>();
  #seq = 0;
  readonly #order = new Map<string, number>();

  constructor(opts: { maxSurfaced?: number } = {}) {
    this.maxSurfaced = opts.maxSurfaced ?? 3;
  }

  #move<P>(id: string, to: ProposalState, patch: Partial<Proposal<P>> = {}): Proposal<P> {
    const p = this.#items.get(id);
    if (!p) throw new IllegalTransition(`no proposal ${id}`);
    if (!NEXT[p.state].includes(to)) throw new IllegalTransition(`${id}: ${p.state} -> ${to}`);
    const q = { ...p, ...patch, state: to } as Proposal<P>;
    this.#items.set(id, q);
    return q;
  }

  enqueue<P>(p: { id: string; kind: ProposalKind; payload: P; summary: string; expiresAt?: number | null }, now: number): Proposal<P> {
    if (this.#items.has(p.id)) throw new IllegalTransition(`proposal ${p.id} already queued`);
    const item: Proposal<P> = {
      id: p.id,
      kind: p.kind,
      payload: p.payload,
      summary: p.summary,
      createdAt: now,
      expiresAt: p.expiresAt ?? null,
      state: 'queued',
      resolution: null,
      surfacedAt: null,
      veiled: false,
    };
    this.#items.set(p.id, item);
    this.#order.set(p.id, this.#seq++);
    return item;
  }

  /** Applies the budget: expire, veil / unveil, then surface into free slots. */
  tick(budget: InterruptionBudget, now: number): QueueEvents {
    const expired: Proposal[] = [];
    const veiled: Proposal[] = [];
    const unveiled: Proposal[] = [];
    const surfaced: Proposal[] = [];
    for (const p of [...this.#items.values()]) {
      if ((p.state === 'queued' || p.state === 'surfaced') && p.expiresAt !== null && now >= p.expiresAt) {
        expired.push(this.#move(p.id, 'expired'));
      }
    }
    const suppressed = budget.mode === 'suppressed';
    for (const p of this.surfaced) {
      if (suppressed && !p.veiled) {
        const q = { ...p, veiled: true };
        this.#items.set(p.id, q);
        veiled.push(q);
      } else if (!suppressed && p.veiled) {
        const q = { ...p, veiled: false };
        this.#items.set(p.id, q);
        unveiled.push(q);
      }
    }
    const free = Math.min(budget.slots, this.maxSurfaced - this.surfaced.length);
    if (!suppressed && free > 0) {
      const waiting = this.queued.sort((a, b) => {
        const ta = a.expiresAt ?? Infinity;
        const tb = b.expiresAt ?? Infinity;
        if (ta !== tb) return ta - tb;
        return this.#order.get(a.id)! - this.#order.get(b.id)!;
      });
      for (const p of waiting.slice(0, free)) surfaced.push(this.#move(p.id, 'surfaced', { surfacedAt: now }));
    }
    return { surfaced, expired, veiled, unveiled };
  }

  /** Only a surfaced proposal can be resolved: the person must have seen it. */
  resolve(id: string, resolution: ProposalResolution): Proposal {
    return this.#move(id, 'resolved', { resolution });
  }

  withdraw(id: string): Proposal {
    return this.#move(id, 'withdrawn');
  }

  get(id: string): Proposal | undefined {
    return this.#items.get(id);
  }

  get all(): Proposal[] {
    return [...this.#items.values()];
  }

  get queued(): Proposal[] {
    return this.all.filter((p) => p.state === 'queued');
  }

  get surfaced(): Proposal[] {
    return this.all.filter((p) => p.state === 'surfaced');
  }
}
