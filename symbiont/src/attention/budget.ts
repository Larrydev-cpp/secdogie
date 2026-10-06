/**
 * The interruption budget: how much SecDogie may put in front of the person
 * right now, judged from recent focus samples.
 *
 *   suppressed  the context is sensitive or being presented: show nothing,
 *               and hide what is already showing;
 *   away        no input for a long while: hold everything, nobody is there;
 *   flow        a sustained high input rate with no pause: hold everything;
 *   engaged     working, not in flow: still hold -- wait for a pause;
 *   gap         a pause after activity, or focus on SecDogie itself: surface.
 *
 * Leaving flow needs a real pause, not a dip in the rate (hysteresis), so a
 * typist who slows down mid-sentence is not interrupted. Nothing here is a
 * timer or a countdown shown to anyone; `nextReviewAt` only tells the
 * scheduler when the answer could change without a new sample.
 */

import type { FocusSample } from './signals.ts';

export type AttentionMode = 'flow' | 'engaged' | 'gap' | 'away' | 'suppressed';

export interface InterruptionBudget {
  readonly mode: AttentionMode;
  /** 0 (do not interrupt) .. 1 (fully available). */
  readonly score: number;
  /** How many proposals may be surfaced now. */
  readonly slots: number;
  readonly cpm: number;
  readonly idleMs: number;
  readonly reasons: readonly string[];
  /** When the mode could change on its own (ms); Infinity if only a sample can change it. */
  readonly nextReviewAt: number;
}

export interface BudgetConfig {
  readonly windowMs: number;
  readonly flowCpm: number;
  readonly flowMinMs: number;
  readonly gapPauseMs: number;
  readonly awayAfterMs: number;
  readonly maxSlots: number;
}

export const DEFAULT_BUDGET: BudgetConfig = {
  windowMs: 90_000,
  flowCpm: 150,
  flowMinMs: 20_000,
  gapPauseMs: 4_000,
  awayAfterMs: 300_000,
  maxSlots: 3,
};

function rate(samples: readonly FocusSample[], from: number): { cpm: number; coveredMs: number } {
  let events = 0;
  let ms = 0;
  for (const s of samples) {
    if (s.at <= from) continue;
    events += s.inputEvents;
    ms += s.sampleMs;
  }
  return { cpm: ms > 0 ? (events * 60_000) / ms : 0, coveredMs: ms };
}

export function evaluateBudget(
  samples: readonly FocusSample[],
  now: number,
  prev: AttentionMode | null = null,
  cfg: BudgetConfig = DEFAULT_BUDGET,
): InterruptionBudget {
  const latest = samples.at(-1);
  if (latest === undefined) {
    return { mode: 'away', score: 0, slots: 0, cpm: 0, idleMs: Infinity, reasons: ['no focus signal yet'], nextReviewAt: Infinity };
  }
  const idleMs = latest.idleMs + Math.max(0, now - latest.at);
  const { cpm } = rate(samples, now - cfg.windowMs);
  const recent = rate(samples, now - cfg.flowMinMs);
  const base = { cpm, idleMs };

  if (latest.context === 'sensitive' || latest.context === 'presenting') {
    return {
      ...base, mode: 'suppressed', score: 0, slots: 0,
      reasons: [latest.context === 'sensitive' ? 'a sensitive context is in front' : 'the screen is being presented'],
      nextReviewAt: Infinity,
    };
  }
  if (idleMs >= cfg.awayAfterMs) {
    return { ...base, mode: 'away', score: 0, slots: 0, reasons: ['no input for a long while'], nextReviewAt: Infinity };
  }
  const toGap = latest.at + (cfg.gapPauseMs - latest.idleMs);
  const toAway = latest.at + (cfg.awayAfterMs - latest.idleMs);
  if (latest.surface === 'secdogie') {
    return {
      ...base, mode: 'gap', score: 1, slots: cfg.maxSlots,
      reasons: ['focus is on SecDogie'], nextReviewAt: toAway,
    };
  }
  if (idleMs >= cfg.gapPauseMs) {
    return {
      ...base, mode: 'gap', score: Math.min(1, 0.6 + (0.4 * idleMs) / 30_000), slots: 1,
      reasons: [`a pause of ${Math.round(idleMs / 1000)}s`], nextReviewAt: toAway,
    };
  }
  const sustained = recent.coveredMs >= cfg.flowMinMs * 0.9 && recent.cpm >= cfg.flowCpm;
  if (sustained || prev === 'flow') {
    return {
      ...base, mode: 'flow', score: 0, slots: 0,
      reasons: [sustained ? `sustained input at ${Math.round(recent.cpm)}/min` : 'still in flow (no pause yet)'],
      nextReviewAt: toGap,
    };
  }
  return { ...base, mode: 'engaged', score: 0.3, slots: 0, reasons: ['working; waiting for a pause'], nextReviewAt: toGap };
}
