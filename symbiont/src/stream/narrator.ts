/**
 * Intent-level narration. Runtime events arrive one by one ("scanned a page",
 * "inserted a delta"); the person reads one line per intent, updated in place:
 * "在 docs.example.com 梳理出 14 个候选状态（看了 3 个页面）". Never a log of
 * steps.
 */

import { zh } from './strings.ts';

export type RuntimeEvent =
  | { readonly type: 'mapped'; readonly origin: string; readonly states: number; readonly at: number }
  | { readonly type: 'synced'; readonly peer: string; readonly deltas: number; readonly at: number }
  | { readonly type: 'fetch-refused'; readonly origin: string; readonly reason: string; readonly at: number }
  | { readonly type: 'aligned' | 'declined' | 'expired'; readonly summary: string; readonly at: number }
  | { readonly type: 'released'; readonly target: string; readonly at: number }
  | { readonly type: 'refused'; readonly target: string; readonly reason: string; readonly at: number };

export interface Line {
  readonly id: string;
  readonly at: number;
  readonly text: string;
}

interface Acc {
  id: string;
  key: string;
  first: number;
  last: number;
  n: number;
  pages: number;
  peers: Set<string>;
  reason: string;
}

const host = (origin: string) => origin.replace(/^https:\/\//, '');

export class Narrator {
  readonly #windowMs: number;
  readonly #open = new Map<string, Acc>();
  #seq = 0;

  constructor(opts: { windowMs?: number } = {}) {
    this.#windowMs = opts.windowMs ?? 60_000;
  }

  /** Folds one event; returns the line to insert or replace (same id = replace). */
  fold(ev: RuntimeEvent): Line {
    // Repeated work on one origin (or syncing in general) folds into one line;
    // a gate outcome is its own line.
    const key =
      ev.type === 'mapped' || ev.type === 'fetch-refused'
        ? `${ev.type}:${ev.origin}`
        : ev.type === 'synced'
          ? 'synced'
          : null;
    let acc = key === null ? undefined : this.#open.get(key);
    if (!acc || ev.at - acc.last > this.#windowMs) {
      acc = { id: `n${++this.#seq}`, key: key ?? '', first: ev.at, last: ev.at, n: 0, pages: 0, peers: new Set(), reason: '' };
      if (key !== null) this.#open.set(key, acc);
    }
    acc.last = ev.at;
    let text: string;
    switch (ev.type) {
      case 'mapped':
        acc.n += ev.states;
        acc.pages += 1;
        text = zh.narrate.mapped(host(ev.origin), acc.n, acc.pages);
        break;
      case 'synced':
        acc.n += ev.deltas;
        acc.peers.add(ev.peer);
        text = zh.narrate.synced(acc.peers.size, acc.n);
        break;
      case 'fetch-refused':
        acc.n += 1;
        acc.reason = ev.reason;
        text = zh.narrate.fetchRefused(host(ev.origin), acc.n, ev.reason);
        break;
      case 'aligned':
        text = zh.narrate.aligned(ev.summary);
        break;
      case 'declined':
        text = zh.narrate.declined(ev.summary);
        break;
      case 'expired':
        text = zh.narrate.expired(ev.summary);
        break;
      case 'released':
        text = zh.narrate.released(ev.target);
        break;
      case 'refused':
        text = zh.narrate.refused(ev.target, ev.reason);
        break;
    }
    return { id: acc.id, at: acc.first, text };
  }
}
