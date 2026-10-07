/**
 * Everything the page says, as one typed table per language. `zh.ts` and
 * `en.ts` are both `Dict`, so the compiler refuses a key that one language has
 * and the other lacks. Nothing here is mechanical: no ids, hashes, DIDs, byte
 * counts or step logs -- those go to DevTools (core/trace.ts).
 */

import type { LinkPhase } from '../net/attach.ts';

/** Every finding the node's gate (citadel FINDING_KINDS) or the page's Gate 1 rules can raise. */
export const FINDING_CODES = [
  // citadel/secdogie_citadel/action_gate.py FINDING_KINDS
  'stale-target', 'target-mismatch', 'no-op', 'repeated', 'polling', 'destructive-chain', 'missing-verification',
  'excessive-cost', 'out-of-capability', 'unattended-posting', 'unauthorized-action', 'intent-unproven',
  'intent-contradiction', 'precondition-failed', 'known-failure',
  // symbiont/src/gate1/rules.ts (the ones not above)
  'empty', 'contradiction', 'overlong', 'target-unproven', 'target-ambiguous', 'graph-destructive',
] as const;
export type FindingCode = (typeof FINDING_CODES)[number];

export interface Dict {
  readonly lang: 'zh-CN' | 'en';
  /** The header label beside the status dot, per link phase. */
  readonly presence: Record<LinkPhase, string>;
  /** The tooltip / description of that label. */
  readonly presenceHint: Record<LinkPhase, string>;
  readonly why: {
    readonly notEnrolled: string;
    readonly anotherTab: string;
    readonly roomFull: string;
    readonly busy: string;
    readonly badLink: string;
    readonly keysUnreadable: string;
    readonly w1Failed: string;
    readonly pairingRejected: string;
    readonly pairingUnavailable: string;
  };
  readonly ambient: {
    readonly idle: string;
    readonly running: string;
    readonly waiting: string;
    readonly unpaired: string;
    readonly connecting: string;
    readonly demo: string;
    readonly framed: string;
  };
  readonly composer: {
    readonly placeholder: string;
    readonly replyPlaceholder: string;
    readonly send: string;
    readonly stop: string;
    readonly offline: string;
  };
  readonly reply: {
    readonly accepted: string;
    readonly refused: string;
    readonly stopped: string;
    readonly done: string;
    readonly notDone: string;
    readonly stillRunning: string;
    readonly undelivered: string;
    readonly expired: string;
  };
  readonly ask: {
    /** The finding said gently, as the lead of the question card. */
    readonly finding: Record<FindingCode, string>;
    readonly confirmStep: (action: string) => string;
    readonly confirmPlan: string;
    readonly continueAsk: string;
    readonly yes: string;
    readonly no: string;
    readonly answered: (answer: string) => string;
    readonly waiting: string;
    readonly expired: string;
  };
  readonly consent: {
    /** "This step will {action}" -- the action is built from the signed fields only. */
    readonly lead: (action: string) => string;
    readonly graveTail: string;
    readonly question: string;
    readonly cancel: string;
    readonly approve: string;
    readonly cannotApprove: string;
    readonly sending: string;
    readonly sent: string;
    readonly cancelled: string;
    readonly waiting: string;
    readonly uncertain: string;
    readonly expired: string;
    readonly refused: string;
    readonly done: string;
    readonly notDone: string;
    readonly tooLong: string;
    readonly quoteLabel: { readonly name: string; readonly text: string; readonly where: string };
  };
  readonly action: {
    readonly click: (name: string) => string;
    readonly clickBlind: string;
    readonly type: (name: string) => string;
    readonly typeBlind: string;
    readonly key: (keys: string) => string;
    readonly submit: (name: string) => string;
    readonly open: (name: string) => string;
    readonly direct: (name: string) => string;
    readonly other: (kind: string, name: string) => string;
    readonly on: (host: string) => string;
    /** Used in the sentence when the target's name is too long to read inline; the card quotes it in full. */
    readonly theTarget: string;
  };
  readonly pairing: {
    readonly checking: string;
    readonly confirm: string;
    readonly compare: string;
    readonly connect: string;
    readonly cancel: string;
    readonly sent: string;
    readonly refused: string;
  };
}
