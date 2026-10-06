/**
 * Renders the stream into the page as ordinary conversation turns.
 *
 * Rules this file keeps, and tests/purity.test.ts enforces:
 *  - no overlay, no `role="dialog"`, no `aria-modal`, no `showModal`, no
 *    `alert` / `confirm` / `prompt`, no `focus()` -- nothing takes the screen
 *    or the keyboard away from the person;
 *  - text only through `textContent`: what a node sends is data, never markup;
 *  - no countdown: a deadline is shown once as a clock time.
 *
 * It is written against a tiny DOM interface so tests can render into a fake
 * document.
 */

import type { Gate2Bubble } from '../gate2/signer_flow.ts';
import type { Gate1Item, Gate2Item, StreamItem } from './stream.ts';
import { zh } from './strings.ts';

export interface El {
  textContent: string | null;
  className: string;
  append(...nodes: El[]): void;
  replaceChildren(...nodes: El[]): void;
  setAttribute(name: string, value: string): void;
  addEventListener(type: string, fn: (ev: unknown) => void): void;
}

export interface InputEl extends El {
  value: string;
}

export interface DocLike {
  createElement(tag: string): El;
}

export interface StreamHandlers {
  answer(probeId: string, text: string): void;
  approve(challengeId: string): void;
  deny(challengeId: string): void;
}

function el(doc: DocLike, tag: string, cls: string, text?: string): El {
  const e = doc.createElement(tag);
  e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function button(doc: DocLike, label: string, cls: string, onClick: () => void, enabled = true): El {
  const b = el(doc, 'button', cls, label);
  b.setAttribute('type', 'button');
  if (!enabled) {
    b.setAttribute('disabled', '');
    b.setAttribute('aria-disabled', 'true');
  }
  b.addEventListener('click', () => onClick());
  return b;
}

export function clockTime(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function renderGate1(doc: DocLike, it: Gate1Item, h: StreamHandlers): El {
  const box = el(doc, 'article', `turn turn-agent bubble gate1 state-${it.state}`);
  box.setAttribute('data-probe', it.id);
  box.append(el(doc, 'div', 'tag', zh.gate1Tag));
  if (it.veiled) {
    box.append(el(doc, 'p', 'veiled', zh.veiled));
    return box;
  }
  if (it.finding) box.append(el(doc, 'div', 'finding', zh.findings[it.finding] ?? it.finding));
  box.append(el(doc, 'p', 'question', it.question));
  if (it.state === 'open') {
    const opts = el(doc, 'div', 'options');
    for (const o of it.options) opts.append(button(doc, o, 'option', () => h.answer(it.id, o)));
    const input = doc.createElement('input') as InputEl;
    input.className = 'free-answer';
    input.setAttribute('type', 'text');
    input.setAttribute('placeholder', zh.answerPlaceholder);
    input.setAttribute('aria-label', zh.answerPlaceholder);
    const send = button(doc, zh.answer, 'send', () => {
      const v = input.value.trim();
      if (v) h.answer(it.id, v);
    });
    const row = el(doc, 'div', 'free');
    row.append(input, send);
    box.append(opts, row);
  } else if (it.state === 'answered' && it.answer !== null) {
    box.append(el(doc, 'p', 'answer', zh.answered + it.answer));
  } else if (it.state === 'expired') {
    box.append(el(doc, 'p', 'note', zh.probeExpired));
  }
  return box;
}

function renderBubble(doc: DocLike, box: El, b: Gate2Bubble, h: StreamHandlers, now: number): void {
  const c = b.challenge;
  const a = c.target_action;
  const head = el(doc, 'div', 'risk');
  head.append(el(doc, 'span', `badge risk-${c.risk_level}`, zh.risk[c.risk_level] ?? c.risk_level));
  head.append(el(doc, 'span', 'until', zh.validUntil(clockTime(c.expires_at))));
  box.append(head);
  if (c.risk_explanation) box.append(el(doc, 'p', 'explanation', c.risk_explanation));
  // Everything the signature commits to, in full.
  const dl = el(doc, 'dl', 'action');
  const rows: Array<[string, string]> = [
    ['kind', a.kind],
    ['target_name', a.target_name],
    ['target_role', a.target_role],
    ['text', a.text],
    ['target_id', a.target_id],
    ['high_risk', a.high_risk ? zh.yes : zh.no],
  ];
  for (const [k, v] of rows) dl.append(el(doc, 'dt', '', zh.field[k] ?? k), el(doc, 'dd', `f-${k}`, v));
  box.append(dl);
  box.append(
    el(doc, 'p', `hash ${b.review.hashMatches ? 'ok' : 'bad'}`,
      `action_hash ${b.review.localHash.slice(0, 12)}…（${b.review.hashMatches ? zh.hashOk : zh.hashBad}）`),
  );
  const stateLine = zh.bubbleState[b.state] ?? '';
  if (stateLine || b.note) box.append(el(doc, 'p', 'note', stateLine + (b.note && b.state === 'refused' ? b.note : '')));
  if (b.state === 'awaiting') {
    const row = el(doc, 'div', 'actions');
    row.append(
      button(doc, zh.approve, 'approve', () => h.approve(b.id), now >= b.armedAt),
      button(doc, zh.deny, 'deny', () => h.deny(b.id)),
    );
    box.append(row);
  } else if (b.state === 'refused') {
    box.append(button(doc, zh.deny, 'deny', () => h.deny(b.id)));
  }
}

function renderGate2(doc: DocLike, it: Gate2Item, h: StreamHandlers, now: number): El {
  const state = it.bubble?.state ?? 'awaiting';
  const box = el(doc, 'article', `turn turn-agent bubble gate2 state-${state}`);
  box.setAttribute('data-challenge', it.id);
  box.append(el(doc, 'div', 'tag', zh.gate2Tag));
  if (it.veiled || it.bubble === null) {
    box.append(el(doc, 'p', 'veiled', zh.veiled));
    return box;
  }
  renderBubble(doc, box, it.bubble, h, now);
  return box;
}

function renderItem(doc: DocLike, it: StreamItem, h: StreamHandlers, now: number): El {
  switch (it.kind) {
    case 'ambient': {
      const box = el(doc, 'section', `ambient mode-${it.mode}`);
      box.setAttribute('aria-live', 'polite');
      box.append(el(doc, 'p', 'swarm', it.swarmLine), el(doc, 'p', 'topology', it.topologyLine));
      if (it.modeLine) box.append(el(doc, 'p', 'mode', it.modeLine));
      return box;
    }
    case 'narration':
      return el(doc, 'p', 'turn narration', it.text);
    case 'gate1':
      return renderGate1(doc, it, h);
    case 'gate2':
      return renderGate2(doc, it, h, now);
    case 'held':
      return el(doc, 'p', 'turn held', it.reason === 'sensitive' ? zh.putAway(it.count) : zh.held(it.count));
  }
}

/** Replaces `root`'s children with the stream. `now` is epoch seconds (for arming). */
export function renderStream(doc: DocLike, root: El, items: readonly StreamItem[], h: StreamHandlers, now: number): void {
  root.replaceChildren(...items.map((it) => renderItem(doc, it, h, now)));
}
