/**
 * The consciousness stream: what the person sees, as data.
 *
 * It is never empty and never asks "what should I do today?" -- the first
 * item is always the ambient state of the swarm and the topology. Below it,
 * in time order: intent-level narration, Gate 1 questions and Gate 2
 * signatures as inline bubbles, and a single "held" line when things wait for
 * a better moment. There are no modal items: everything is a turn in the
 * conversation.
 *
 * Veiling is enforced here, not only in rendering: a veiled bubble's view
 * carries no question, no action and no options.
 */

import type { AttentionMode } from '../attention/budget.ts';
import type { Gate2Bubble } from '../gate2/signer_flow.ts';
import type { Line, RuntimeEvent } from './narrator.ts';
import { Narrator } from './narrator.ts';
import { zh } from './strings.ts';

export interface SwarmStatus {
  readonly peers: number;
  readonly heads: number;
  readonly deltas: number;
  /** Milliseconds; null if never synced. */
  readonly lastSyncAt: number | null;
}

export interface TopologyStatus {
  readonly origins: number;
  readonly states: number;
  readonly references: number;
  readonly recentStates: number;
}

export interface AmbientItem {
  readonly kind: 'ambient';
  readonly id: 'ambient';
  readonly mode: AttentionMode;
  readonly swarmLine: string;
  readonly topologyLine: string;
  readonly modeLine: string;
}

export interface NarrationItem {
  readonly kind: 'narration';
  readonly id: string;
  readonly at: number;
  readonly text: string;
}

export interface Gate1Item {
  readonly kind: 'gate1';
  readonly id: string;
  readonly at: number;
  readonly state: 'open' | 'answered' | 'expired' | 'closed';
  readonly veiled: boolean;
  readonly question: string;
  readonly finding: string;
  readonly options: readonly string[];
  readonly answer: string | null;
}

export interface Gate2Item {
  readonly kind: 'gate2';
  readonly id: string;
  readonly at: number;
  readonly veiled: boolean;
  readonly bubble: Gate2Bubble | null;
}

export interface HeldItem {
  readonly kind: 'held';
  readonly id: 'held';
  readonly count: number;
  /** `waiting`: proposals held for a better moment; `sensitive`: the whole conversation is put away. */
  readonly reason: 'waiting' | 'sensitive';
}

export type StreamItem = AmbientItem | NarrationItem | Gate1Item | Gate2Item | HeldItem;
type TimelineItem = NarrationItem | Gate1Item | Gate2Item;

export class ConsciousnessStream {
  readonly #narrator: Narrator;
  readonly #timeline = new Map<string, TimelineItem>();
  readonly #clock: () => number;
  #ambient: AmbientItem = {
    kind: 'ambient',
    id: 'ambient',
    mode: 'away',
    swarmLine: zh.ambient.swarm(0, 0, 0) + zh.ambient.neverSynced,
    topologyLine: zh.ambient.topology(0, 0, 0),
    modeLine: zh.ambient.mode['away']!,
  };
  #held = 0;
  #listeners: Array<() => void> = [];

  constructor(opts: { clock?: () => number; narrator?: Narrator } = {}) {
    this.#clock = opts.clock ?? (() => Date.now());
    this.#narrator = opts.narrator ?? new Narrator();
  }

  onChange(fn: () => void): () => void {
    this.#listeners.push(fn);
    return () => {
      this.#listeners = this.#listeners.filter((f) => f !== fn);
    };
  }

  #changed(): void {
    for (const fn of this.#listeners) fn();
  }

  setAmbient(swarm: SwarmStatus, topology: TopologyStatus, mode: AttentionMode): void {
    const now = this.#clock();
    const synced =
      swarm.lastSyncAt === null ? zh.ambient.neverSynced : zh.ambient.synced(Math.round((now - swarm.lastSyncAt) / 1000));
    this.#ambient = {
      kind: 'ambient',
      id: 'ambient',
      mode,
      swarmLine: zh.ambient.swarm(swarm.peers, swarm.heads, swarm.deltas) + synced,
      topologyLine:
        zh.ambient.topology(topology.origins, topology.states, topology.references) +
        (topology.recentStates > 0 ? zh.ambient.recent(topology.recentStates) : ''),
      modeLine: zh.ambient.mode[mode] ?? '',
    };
    this.#changed();
  }

  narrate(ev: RuntimeEvent): Line {
    const line = this.#narrator.fold(ev);
    this.#timeline.set(line.id, { kind: 'narration', id: line.id, at: line.at, text: line.text });
    this.#changed();
    return line;
  }

  putGate1(item: Omit<Gate1Item, 'kind'>): void {
    this.#timeline.set(`g1:${item.id}`, { kind: 'gate1', ...item });
    this.#changed();
  }

  putGate2(item: Omit<Gate2Item, 'kind'>): void {
    this.#timeline.set(`g2:${item.id}`, { kind: 'gate2', ...item });
    this.#changed();
  }

  gate1(id: string): Gate1Item | undefined {
    const it = this.#timeline.get(`g1:${id}`);
    return it?.kind === 'gate1' ? it : undefined;
  }

  gate2(id: string): Gate2Item | undefined {
    const it = this.#timeline.get(`g2:${id}`);
    return it?.kind === 'gate2' ? it : undefined;
  }

  /** Follows the attention mode; a suppressed mode puts the whole conversation away. */
  setMode(mode: AttentionMode): void {
    if (mode === this.#ambient.mode) return;
    this.#ambient = { ...this.#ambient, mode, modeLine: zh.ambient.mode[mode] ?? '' };
    this.#changed();
  }

  setHeld(count: number): void {
    if (count !== this.#held) {
      this.#held = count;
      this.#changed();
    }
  }

  /**
   * The view, ambient first. Veiled bubbles carry no content, and while the
   * mode is suppressed (a sensitive or presented screen) nothing of the
   * conversation is in the view at all -- only how much is put away.
   */
  items(): StreamItem[] {
    if (this.#ambient.mode === 'suppressed') {
      const hidden = this.#timeline.size + this.#held;
      return hidden > 0 ? [this.#ambient, { kind: 'held', id: 'held', count: hidden, reason: 'sensitive' }] : [this.#ambient];
    }
    const timeline = [...this.#timeline.values()].sort((a, b) => a.at - b.at).map((it): TimelineItem => {
      if (it.kind === 'gate1' && it.veiled) return { ...it, question: '', options: [], answer: null, finding: '' };
      if (it.kind === 'gate2' && it.veiled) return { ...it, bubble: null };
      return it;
    });
    const out: StreamItem[] = [this.#ambient, ...timeline];
    if (this.#held > 0) out.push({ kind: 'held', id: 'held', count: this.#held, reason: 'waiting' });
    return out;
  }
}
