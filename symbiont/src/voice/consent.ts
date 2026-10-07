/**
 * The Gate 2 card's words, made here from the signed action alone: the six
 * fields the operator's signature covers (`secdogie/action-authorization/v1`).
 * Nothing the node says about the action (its risk explanation, its risk
 * level) is trusted for the wording -- that goes to DevTools. Severity only
 * ever goes up: a high-risk flag, a deleting key or a deleting word makes the
 * step grave ("cannot be undone"), whatever the node said.
 */

import { mentionsDestruction } from '../gate1/rules.ts';
import type { TargetAction } from '../gate2/authz.ts';
import type { Dict } from './dict.ts';
import { MAX_QUOTE, visible } from './visible.ts';

export interface QuoteLine {
  readonly label: string;
  /** Verbatim, with hidden characters made visible. */
  readonly text: string;
}

export interface ConsentView {
  readonly sentence: string;
  readonly quote: readonly QuoteLine[];
  readonly grave: boolean;
  /** Too long to show in full: the card refuses to let it be approved. */
  readonly tooLong: boolean;
}

const CLICK = new Set(['click', 'left_click', 'right_click', 'double_click', 'tap', 'press', 'select', 'check', 'toggle']);
const TYPE = new Set(['type', 'input', 'fill', 'paste']);
const KEY = new Set(['key', 'hotkey', 'keys', 'shortcut']);
const SUBMIT = new Set(['submit', 'send', 'post', 'confirm']);
const OPEN = new Set(['navigate', 'open', 'goto', 'visit']);
const GRAVE_KEYS = /(^|\+)(delete|del|backspace|shift\+delete)$/i;
const INLINE_NAME = 40;

function httpsWhere(targetId: string): { host: string; where: string } | null {
  try {
    const u = new URL(targetId);
    if (u.protocol !== 'https:') return null;
    return { host: u.host, where: `${u.host}${u.pathname}${u.search}` };
  } catch {
    return null;
  }
}

/** "ctrl+s" -> "Ctrl+S", "delete" -> "Delete". */
export function keysLabel(text: string): string {
  return text
    .split('+')
    .map((k) => (k.length <= 1 ? k.toUpperCase() : k.charAt(0).toUpperCase() + k.slice(1).toLowerCase()))
    .join('+');
}

export function consentView(d: Dict, a: TargetAction): ConsentView {
  const kind = a.kind.toLowerCase();
  const rawName = a.target_name;
  const where = httpsWhere(a.target_id);
  const name = rawName && [...rawName].length <= INLINE_NAME ? visible(rawName) : rawName ? d.action.theTarget : '';
  const destructiveName = !!rawName && mentionsDestruction(rawName);
  const graveKey = KEY.has(kind) && GRAVE_KEYS.test(a.text.trim());
  const grave = a.high_risk || graveKey || destructiveName || mentionsDestruction(kind);

  let action: string;
  if (CLICK.has(kind)) action = !name ? d.action.clickBlind : destructiveName ? d.action.direct(name) : d.action.click(name);
  else if (TYPE.has(kind)) action = name ? d.action.type(name) : d.action.typeBlind;
  else if (KEY.has(kind)) action = d.action.key(visible(keysLabel(a.text.trim()) || a.text));
  else if (SUBMIT.has(kind)) action = !name ? d.action.other(visible(a.kind), '') : destructiveName ? d.action.direct(name) : d.action.submit(name);
  else if (OPEN.has(kind)) action = d.action.open(name || (where?.host ?? ''));
  else action = d.action.other(visible(a.kind), name);
  if (where) action += d.action.on(where.host);

  const quote: QuoteLine[] = [];
  if (rawName && (name !== visible(rawName) || /[\p{Cc}\p{Cf}]/u.test(rawName))) quote.push({ label: d.consent.quoteLabel.name, text: visible(rawName) });
  if (a.text && !KEY.has(kind)) quote.push({ label: d.consent.quoteLabel.text, text: visible(a.text) });
  if (where) quote.push({ label: d.consent.quoteLabel.where, text: visible(where.where) });
  const tooLong = quote.reduce((n, q) => n + [...q.text].length, 0) > MAX_QUOTE;

  const end = d.lang === 'en' ? '.' : '。';
  const sentence = `${d.consent.lead(action)}${grave ? d.consent.graveTail : end}${d.lang === 'en' ? ' ' : ''}${d.consent.question}`;
  return { sentence, quote, grave, tooLong };
}
