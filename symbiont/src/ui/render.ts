/**
 * The conversation, drawn: one column of turns. Your words on the right, the
 * symbiont's on the left, and two kinds of inline card -- a gentle question
 * (Gate 1) and a frosted consent card with exactly two buttons (Gate 2). No
 * overlay, no modal, no focus grab, no countdown, no step log, nothing
 * mechanical: the turns arrive here already in words (client/core_link.ts).
 *
 * Rendering is keyed: a turn is rebuilt only when it changed, so a card that
 * is on screen is never re-created under the pointer. Approve on a consent
 * card fades in and only works once it has been on screen for a moment.
 */

import type { Turn } from '../client/core_link.ts';
import type { QuoteLine } from '../voice/consent.ts';
import type { Dict } from '../voice/dict.ts';
import { type DocLike, type El, button, el, setEnabled } from './dom.ts';

export interface Handlers {
  answer(turnId: string, value: string): void;
  approve(turnId: string): void;
  cancel(turnId: string): void;
  confirmPairing(): void;
  cancelPairing(): void;
}

interface Drawn {
  readonly rev: number;
  readonly el: El;
  readonly approve: El | null;
  readonly armedAt: number;
}

function quote(doc: DocLike, lines: readonly QuoteLine[]): El | null {
  if (!lines.length) return null;
  const box = el(doc, 'div', 'quote');
  for (const l of lines) {
    const row = el(doc, 'div', 'q');
    row.append(el(doc, 'span', 'q-label', l.label), el(doc, 'span', 'q-text', l.text));
    box.append(row);
  }
  return box;
}

export class ThreadView {
  readonly #doc: DocLike;
  readonly #root: El;
  readonly #d: Dict;
  readonly #h: Handlers;
  readonly #drawn = new Map<string, Drawn>();

  constructor(doc: DocLike, root: El, voice: Dict, handlers: Handlers) {
    this.#doc = doc;
    this.#root = root;
    this.#d = voice;
    this.#h = handlers;
  }

  /** Draws `turns` (rebuilding only what changed); returns when the next Approve arms, or null. */
  render(turns: readonly Turn[], now: number): number | null {
    const keep = new Set<string>();
    const nodes: El[] = [];
    for (const t of turns) {
      keep.add(t.id);
      let d = this.#drawn.get(t.id);
      if (!d || d.rev !== t.rev) {
        d = this.#build(t, now);
        this.#drawn.set(t.id, d);
      }
      nodes.push(d.el);
    }
    for (const id of [...this.#drawn.keys()]) if (!keep.has(id)) this.#drawn.delete(id);
    this.#root.replaceChildren(...nodes);
    return this.arm(now);
  }

  /** Enables each Approve whose moment has come; returns the next arming time, or null. */
  arm(now: number): number | null {
    let next: number | null = null;
    for (const d of this.#drawn.values()) {
      if (!d.approve) continue;
      const armed = now >= d.armedAt;
      setEnabled(d.approve, armed);
      if (!armed && (next === null || d.armedAt < next)) next = d.armedAt;
    }
    return next;
  }

  #build(t: Turn, now: number): Drawn {
    const doc = this.#doc;
    const d = this.#d;
    const li = el(doc, 'li', `turn ${t.kind}`);
    li.setAttribute('data-turn', t.id);
    let approve: El | null = null;
    let armedAt = 0;
    switch (t.kind) {
      case 'you':
        li.append(el(doc, 'p', 'words', t.text));
        break;
      case 'say':
        li.className = `turn say tone-${t.tone}`;
        li.append(el(doc, 'p', 'words', t.text));
        break;
      case 'ask': {
        li.className = `turn card ask state-${t.state}`;
        if (t.lead) li.append(el(doc, 'p', 'lead', t.lead));
        if (t.question) li.append(el(doc, 'p', 'question', t.question));
        const q = quote(doc, t.quote);
        if (q) li.append(q);
        if (t.state === 'open' && t.options.length) {
          const chips = el(doc, 'div', 'chips');
          for (const o of t.options) chips.append(button(doc, o.label, 'chip', () => this.#h.answer(t.id, o.value)));
          li.append(chips);
        }
        const note = t.state === 'answered' && t.answer !== null ? d.ask.answered(t.answer)
          : t.state === 'waiting' ? d.ask.waiting : t.state === 'expired' ? d.ask.expired : '';
        if (note) li.append(el(doc, 'p', 'note', note));
        break;
      }
      case 'consent': {
        li.className = `turn card consent state-${t.state}${t.grave ? ' grave' : ''}`;
        li.append(el(doc, 'p', 'sentence', t.sentence));
        const q = quote(doc, t.quote);
        if (q) li.append(q);
        if (t.state === 'awaiting') {
          const actions = el(doc, 'div', 'actions');
          approve = button(doc, d.consent.approve, 'approve', () => this.#h.approve(t.id));
          actions.append(button(doc, d.consent.cancel, 'cancel', () => this.#h.cancel(t.id)), approve);
          li.append(actions);
          if (!t.canApprove || t.tooLong) {
            setEnabled(approve, false);
            li.append(el(doc, 'p', 'note', t.tooLong ? d.consent.tooLong : d.consent.cannotApprove));
            approve = null; // never armed
          } else {
            armedAt = t.armedAt;
            setEnabled(approve, now >= armedAt);
          }
        } else {
          const notes: Record<string, string> = {
            sending: d.consent.sending, sent: d.consent.sent, cancelled: d.consent.cancelled, waiting: d.consent.waiting,
            uncertain: d.consent.uncertain, expired: d.consent.expired, refused: d.consent.refused, done: d.consent.done,
            'not-done': d.consent.notDone,
          };
          li.append(el(doc, 'p', 'note', notes[t.state] ?? ''));
        }
        break;
      }
      case 'pairing': {
        li.className = `turn card pairing stage-${t.stage}`;
        if (t.stage === 'checking') {
          li.append(el(doc, 'p', 'lead', d.pairing.checking));
          break;
        }
        li.append(el(doc, 'p', 'lead', d.pairing.confirm));
        if (t.code) li.append(el(doc, 'p', 'code', t.code));
        if (t.stage === 'confirm') {
          li.append(el(doc, 'p', 'note', d.pairing.compare));
          const actions = el(doc, 'div', 'actions');
          actions.append(
            button(doc, d.pairing.cancel, 'cancel', () => this.#h.cancelPairing()),
            button(doc, d.pairing.connect, 'approve', () => this.#h.confirmPairing()),
          );
          li.append(actions);
        } else {
          li.append(el(doc, 'p', 'note', t.stage === 'sent' ? d.pairing.sent : d.pairing.refused));
        }
        break;
      }
    }
    return { rev: t.rev, el: li, approve, armedAt };
  }
}
