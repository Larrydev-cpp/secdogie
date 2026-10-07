/**
 * CURRENT_STATUS and the node's other status lines, read into what the page
 * needs: whether work is running, whether a step waits for the operator, and a
 * plain sentence for the conversation when one is due. The node's own words
 * (summaries, refusal reasons) go to DevTools; the page says it in its own.
 */

import type { Dict } from './dict.ts';

export type StatusLine =
  | { readonly kind: 'current'; readonly running: boolean; readonly waiting: boolean; readonly goal: string }
  | { readonly kind: 'accepted' }
  | { readonly kind: 'stopped' }
  | { readonly kind: 'refused'; readonly detail: string }
  | { readonly kind: 'finished'; readonly goal: string; readonly ok: boolean; readonly detail: string }
  | { readonly kind: 'answer-adopted' }
  | { readonly kind: 'answer-expired' }
  | { readonly kind: 'other'; readonly detail: string };

const FINISHED = /^goal (\S+) finished: exit (-?\d+)(?: -- ([\s\S]*))?$/;

export function readStatus(content: string, about: string): StatusLine {
  if (content === 'status: idle') return { kind: 'current', running: false, waiting: false, goal: '' };
  if (content === 'status: running') return { kind: 'current', running: true, waiting: false, goal: about };
  if (content === 'status: waiting for operator') return { kind: 'current', running: true, waiting: true, goal: about };
  if (content === 'answer adopted') return { kind: 'answer-adopted' };
  if (content.startsWith('no answer in time')) return { kind: 'answer-expired' };
  const f = FINISHED.exec(content);
  if (f) return { kind: 'finished', goal: f[1]!, ok: f[2] === '0', detail: f[3] ?? '' };
  if (content.startsWith('accepted: stop requested')) return { kind: 'stopped' };
  if (content.startsWith('accepted')) return { kind: 'accepted' };
  if (content.startsWith('refused')) return { kind: 'refused', detail: content };
  return { kind: 'other', detail: content };
}

/** The conversation line a status deserves, or null for none. */
export function statusSentence(d: Dict, s: StatusLine): { text: string; tone: 'plain' | 'done' | 'problem' } | null {
  switch (s.kind) {
    case 'accepted':
      return { text: d.reply.accepted, tone: 'plain' };
    case 'stopped':
      return { text: d.reply.stopped, tone: 'plain' };
    case 'refused':
      return { text: d.reply.refused, tone: 'problem' };
    case 'finished':
      return s.ok ? { text: d.reply.done, tone: 'done' } : { text: d.reply.notDone, tone: 'problem' };
    case 'answer-expired':
      return { text: d.reply.expired, tone: 'problem' };
    default:
      return null;
  }
}
