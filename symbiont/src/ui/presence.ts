/**
 * The header: one status dot and its label ("共生体: 已连接"), with a plain
 * explanation for the tooltip and for screen readers. Never a number, a DID
 * or an address.
 */

import type { Activity, Presence } from '../client/core_link.ts';
import type { Dict } from '../voice/dict.ts';
import type { El } from './dom.ts';

const WHY: Record<string, keyof Dict['why']> = {
  'not-enrolled': 'notEnrolled',
  'another-tab': 'anotherTab',
  'room-full': 'roomFull',
  busy: 'busy',
  'bad-link': 'badLink',
  'keys-unreadable': 'keysUnreadable',
  'w1-failed': 'w1Failed',
  'pairing-rejected': 'pairingRejected',
  'pairing-unavailable': 'pairingUnavailable',
};

export function presenceHint(d: Dict, p: Presence): string {
  const why = p.reason ? WHY[p.reason] : undefined;
  return why ? d.why[why] : d.presenceHint[p.phase];
}

export function ambientLine(d: Dict, p: Presence, a: Activity, empty: boolean): string {
  switch (p.phase) {
    case 'connected':
    case 'demo':
      if (a.waiting) return d.ambient.waiting;
      if (a.running) return d.ambient.running;
      return empty ? (p.phase === 'demo' ? d.ambient.demo : d.ambient.idle) : '';
    case 'unpaired':
      return p.reason && WHY[p.reason] ? d.why[WHY[p.reason]!] : d.ambient.unpaired;
    case 'connecting':
    case 'reconnecting':
      return empty ? d.ambient.connecting : '';
    case 'pairing':
      return '';
    default:
      return presenceHint(d, p);
  }
}

export function drawPresence(d: Dict, p: Presence, parts: { dot: El; label: El; hint: El; group: El }): void {
  parts.dot.setAttribute('data-phase', p.phase);
  parts.label.textContent = d.presence[p.phase];
  const hint = presenceHint(d, p);
  parts.hint.textContent = hint;
  parts.group.setAttribute('title', hint);
}
