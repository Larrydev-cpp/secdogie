// Subsystem B: the interruption budget and the proposal queue.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { DEFAULT_BUDGET, evaluateBudget } from '../src/attention/budget.ts';
import { IllegalTransition, ProposalQueue } from '../src/attention/queue.ts';
import { AttentionScheduler } from '../src/attention/scheduler.ts';
import { type ContextClass, type FocusSample, ManualFocusProvider } from '../src/attention/signals.ts';

/** One-second samples: `cpm` presses per minute, last press `idleMs` ago. */
export function typing(fromMs: number, seconds: number, cpm: number, context: ContextClass = 'work'): FocusSample[] {
  const out: FocusSample[] = [];
  for (let i = 1; i <= seconds; i++) {
    out.push({ at: fromMs + i * 1000, surface: 'other', context, inputEvents: cpm / 60, sampleMs: 1000, idleMs: cpm > 0 ? 100 : i * 1000 });
  }
  return out;
}

export function pause(fromMs: number, seconds: number, context: ContextClass = 'work', surface: FocusSample['surface'] = 'other'): FocusSample[] {
  const out: FocusSample[] = [];
  for (let i = 1; i <= seconds; i++) {
    out.push({ at: fromMs + i * 1000, surface, context, inputEvents: 0, sampleMs: 1000, idleMs: i * 1000 });
  }
  return out;
}

test('sustained fast typing is flow; a pause opens a gap', () => {
  const s = typing(0, 30, 240);
  const b = evaluateBudget(s, 30_000);
  assert.equal(b.mode, 'flow');
  assert.equal(b.slots, 0);
  assert.ok(b.cpm >= 200);
  assert.equal(b.nextReviewAt, 30_000 + DEFAULT_BUDGET.gapPauseMs - 100);

  const after = [...s, ...pause(30_000, 5)];
  const g = evaluateBudget(after, 35_000, 'flow');
  assert.equal(g.mode, 'gap');
  assert.equal(g.slots, 1);
});

test('slowing down mid-flow is not a gap (hysteresis)', () => {
  const s = [...typing(0, 30, 240), ...typing(30_000, 20, 60)];
  assert.equal(evaluateBudget(s, 50_000, 'flow').mode, 'flow');
  assert.equal(evaluateBudget(s, 50_000, null).mode, 'engaged');
});

test('sensitive and presenting contexts suppress; long idleness is away', () => {
  assert.equal(evaluateBudget(typing(0, 5, 30, 'sensitive'), 5000).mode, 'suppressed');
  assert.equal(evaluateBudget(pause(0, 5, 'presenting'), 5000).mode, 'suppressed');
  assert.equal(evaluateBudget(pause(0, 5), 400_000).mode, 'away');
  assert.equal(evaluateBudget([], 0).slots, 0);
});

test('looking at SecDogie opens the most room', () => {
  const b = evaluateBudget(pause(0, 1, 'work', 'secdogie'), 1000);
  assert.equal(b.mode, 'gap');
  assert.equal(b.slots, DEFAULT_BUDGET.maxSlots);
});

test('the queue holds during flow and surfaces the earliest deadline first', () => {
  const q = new ProposalQueue();
  q.enqueue({ id: 'later', kind: 'gate1', payload: null, summary: 'a question', expiresAt: 90_000 }, 0);
  q.enqueue({ id: 'sooner', kind: 'gate2', payload: null, summary: 'a signature', expiresAt: 60_000 }, 1);
  q.enqueue({ id: 'open', kind: 'gate1', payload: null, summary: 'no deadline' }, 2);
  const flow = evaluateBudget(typing(0, 30, 240), 30_000);
  assert.deepEqual(q.tick(flow, 30_000).surfaced, []);
  const gap = evaluateBudget([...typing(0, 30, 240), ...pause(30_000, 5)], 35_000, 'flow');
  assert.deepEqual(q.tick(gap, 35_000).surfaced.map((p) => p.id), ['sooner']);
});

test('expiry is final, and a queued proposal cannot be resolved unseen', () => {
  const q = new ProposalQueue();
  q.enqueue({ id: 'c', kind: 'gate2', payload: null, summary: 's', expiresAt: 10 }, 0);
  assert.throws(() => q.resolve('c', 'approved'), IllegalTransition);
  const ev = q.tick(evaluateBudget(typing(0, 30, 240), 30_000), 30_000);
  assert.deepEqual(ev.expired.map((p) => p.id), ['c']);
  assert.throws(() => q.resolve('c', 'approved'), IllegalTransition);
  assert.throws(() => q.withdraw('c'), IllegalTransition);
});

test('a sensitive context veils what is showing, and unveils it after', () => {
  const q = new ProposalQueue();
  q.enqueue({ id: 'x', kind: 'gate2', payload: null, summary: 's' }, 0);
  q.tick(evaluateBudget(pause(0, 5), 5000), 5000);
  assert.equal(q.get('x')!.state, 'surfaced');
  const v = q.tick(evaluateBudget(pause(5000, 1, 'sensitive'), 6000), 6000);
  assert.deepEqual(v.veiled.map((p) => p.id), ['x']);
  const u = q.tick(evaluateBudget(pause(6000, 5), 11_000), 11_000);
  assert.deepEqual(u.unveiled.map((p) => p.id), ['x']);
});

test('the scheduler reviews by itself when a pause would open a gap', () => {
  const clock = { t: 0 };
  const timers: Array<{ at: number; fn: () => void }> = [];
  const q = new ProposalQueue();
  const sched = new AttentionScheduler({
    queue: q,
    clock: () => clock.t,
    timer: (fn, ms) => {
      timers.push({ at: clock.t + ms, fn });
      return () => {};
    },
  });
  const provider = new ManualFocusProvider();
  sched.attach(provider);
  for (const s of typing(0, 30, 240)) {
    clock.t = s.at;
    provider.push(s);
  }
  q.enqueue({ id: 'p', kind: 'gate1', payload: null, summary: 's' }, clock.t);
  sched.review();
  assert.equal(sched.budget.mode, 'flow');
  assert.equal(q.get('p')!.state, 'queued');
  const next = timers.at(-1)!;
  clock.t = next.at;
  next.fn();
  assert.equal(sched.budget.mode, 'gap');
  assert.equal(q.get('p')!.state, 'surfaced');
});
