/**
 * The Gate 1 intent contract -- `IntentContract` and its two checks from
 * `citadel/secdogie_citadel/action_gate.py`. A mutating step must say what it
 * is for; a destructive one must also say how to back out, or acknowledge that
 * it cannot. The gate cannot invent a purpose or a rollback, so a gap becomes
 * a question to the human, never a filled-in default.
 */

import type { Finding } from './rules.ts';

export interface IntentContract {
  readonly purpose: string;
  readonly rollback: string;
  /** An acknowledgment, not a pass: an irreversible destructive step still needs Gate 2. */
  readonly irreversible: boolean;
  readonly requiresPresent: readonly string[];
}

export const emptyContract: IntentContract = { purpose: '', rollback: '', irreversible: false, requiresPresent: [] };

export function checkContract(c: IntentContract, step: { mutating: boolean; destructive: boolean }): Finding | null {
  if (c.irreversible && c.rollback.trim()) {
    return {
      code: 'intent-contradiction',
      reason: 'intent contradicts itself: declared irreversible yet claims a rollback',
      suggestion: 'Say which one holds: can it be undone or not?',
    };
  }
  if (!step.mutating) return null;
  const missing: string[] = [];
  if (!c.purpose.trim()) missing.push('its purpose (which goal it serves)');
  if (step.destructive && !c.rollback.trim() && !c.irreversible) {
    missing.push('a rollback path, or an explicit irreversible acknowledgment');
  }
  if (missing.length === 0) return null;
  return {
    code: 'intent-unproven',
    reason: `intent not stated: ${missing.join('; ')}`,
    suggestion: 'State how to back out, or acknowledge that it cannot be undone.',
  };
}
