/**
 * Ties focus samples, the budget and the proposal queue together. Each new
 * sample -- or the moment the budget said it might change -- re-evaluates the
 * budget and ticks the queue; listeners get what surfaced, expired, or was
 * veiled. The clock and the timer are injectable, so the whole thing runs
 * deterministically in tests.
 */

import { type AttentionMode, type BudgetConfig, DEFAULT_BUDGET, type InterruptionBudget, evaluateBudget } from './budget.ts';
import type { ProposalQueue, QueueEvents } from './queue.ts';
import type { FocusProvider, FocusSample } from './signals.ts';

export type Timer = (fn: () => void, ms: number) => () => void;

const realTimer: Timer = (fn, ms) => {
  const h = setTimeout(fn, ms);
  return () => clearTimeout(h);
};

export class AttentionScheduler {
  readonly queue: ProposalQueue;
  readonly #cfg: BudgetConfig;
  readonly #clock: () => number;
  readonly #timer: Timer | null;
  #samples: FocusSample[] = [];
  #budget: InterruptionBudget;
  #prev: AttentionMode | null = null;
  #cancel: (() => void) | null = null;
  #listeners: Array<(e: QueueEvents, b: InterruptionBudget) => void> = [];

  constructor(opts: { queue: ProposalQueue; config?: BudgetConfig; clock?: () => number; timer?: Timer | null }) {
    this.queue = opts.queue;
    this.#cfg = opts.config ?? DEFAULT_BUDGET;
    this.#clock = opts.clock ?? (() => performance.now());
    this.#timer = opts.timer === undefined ? realTimer : opts.timer;
    this.#budget = evaluateBudget([], this.#clock(), null, this.#cfg);
  }

  get budget(): InterruptionBudget {
    return this.#budget;
  }

  onTick(fn: (e: QueueEvents, b: InterruptionBudget) => void): () => void {
    this.#listeners.push(fn);
    return () => {
      this.#listeners = this.#listeners.filter((f) => f !== fn);
    };
  }

  /** Starts listening to a provider; returns a stop function. */
  attach(provider: FocusProvider): () => void {
    return provider.start((s) => this.observe(s));
  }

  observe(s: FocusSample): QueueEvents {
    this.#samples.push(s);
    const horizon = s.at - this.#cfg.windowMs;
    while (this.#samples.length > 1 && this.#samples[0]!.at < horizon) this.#samples.shift();
    return this.review();
  }

  /** Re-evaluates now (also called when the budget's review time comes). */
  review(now = this.#clock()): QueueEvents {
    this.#budget = evaluateBudget(this.#samples, now, this.#prev, this.#cfg);
    this.#prev = this.#budget.mode;
    const events = this.queue.tick(this.#budget, now);
    for (const fn of this.#listeners) fn(events, this.#budget);
    this.#arm(now);
    return events;
  }

  #arm(now: number): void {
    this.#cancel?.();
    this.#cancel = null;
    const deadlines = this.queue.all
      .filter((p) => (p.state === 'queued' || p.state === 'surfaced') && p.expiresAt !== null)
      .map((p) => p.expiresAt!);
    const at = Math.min(this.#budget.nextReviewAt, ...deadlines);
    if (this.#timer && Number.isFinite(at)) this.#cancel = this.#timer(() => this.review(), Math.max(0, at - now));
  }

  stop(): void {
    this.#cancel?.();
    this.#cancel = null;
  }
}
