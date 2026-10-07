/**
 * The conversation as the page shows it: prepared sentences only (see
 * core_link.ts). Cards that ask something of the person (a question, a
 * consent) are held back while the person is typing or away, and appear when
 * they are free -- never in the middle of a sentence.
 */

import type { Turn } from './core_link.ts';

const KEEP = 200;

type Draft<T extends Turn> = T extends Turn ? Omit<T, 'id' | 'rev'> : never;

export class Conversation {
  readonly #turns: Turn[] = [];
  readonly #held: Turn[] = [];
  readonly #listeners = new Set<() => void>();
  #next = 0;
  #holding = false;

  onChange(cb: () => void): () => void {
    this.#listeners.add(cb);
    return () => this.#listeners.delete(cb);
  }

  list(): readonly Turn[] {
    return this.#turns;
  }

  get holding(): boolean {
    return this.#holding;
  }

  /** Adds a turn; a question or consent waits while the person is busy. Returns its id. */
  add<T extends Turn>(draft: Draft<T>): string {
    const turn = { ...draft, id: `t${++this.#next}`, rev: 1 } as Turn;
    if (this.#holding && (turn.kind === 'ask' || turn.kind === 'consent')) this.#held.push(turn);
    else this.#push(turn);
    this.#changed();
    return turn.id;
  }

  get(id: string): Turn | undefined {
    return this.#turns.find((t) => t.id === id) ?? this.#held.find((t) => t.id === id);
  }

  update(id: string, patch: Partial<Turn>): void {
    for (const list of [this.#turns, this.#held]) {
      const i = list.findIndex((t) => t.id === id);
      if (i >= 0) {
        list[i] = { ...list[i]!, ...patch, rev: list[i]!.rev + 1 } as Turn;
        this.#changed();
        return;
      }
    }
  }

  remove(id: string): void {
    for (const list of [this.#turns, this.#held]) {
      const i = list.findIndex((t) => t.id === id);
      if (i >= 0) {
        list.splice(i, 1);
        this.#changed();
        return;
      }
    }
  }

  hold(on: boolean): void {
    if (on === this.#holding) return;
    this.#holding = on;
    if (!on && this.#held.length) {
      for (const t of this.#held.splice(0)) this.#push(t);
      this.#changed();
    }
  }

  #push(t: Turn): void {
    this.#turns.push(t);
    if (this.#turns.length > KEEP) this.#turns.splice(0, this.#turns.length - KEEP);
  }

  #changed(): void {
    for (const cb of this.#listeners) cb();
  }
}
