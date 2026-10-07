/**
 * The one interface the page talks to. The real one (`OperatorClient` over
 * the WebRTC attachment) and the demo (`DemoCore`, an in-page node speaking
 * the same packets) both implement it.
 *
 * Everything in it is already in words: a turn holds the sentences to show
 * and never an id, a hash, a DID or a byte count. Turn ids are local counters
 * ("t7"), meaningful only to this page.
 */

import type { LinkPhase } from '../net/attach.ts';
import type { QuoteLine } from '../voice/consent.ts';
import type { OptionView } from '../voice/question.ts';

export type Tone = 'plain' | 'done' | 'problem' | 'quiet';

export type AskState = 'open' | 'answered' | 'expired' | 'waiting';

export type ConsentState =
  | 'awaiting'
  | 'sending'
  | 'sent'
  | 'cancelled'
  | 'waiting'
  | 'uncertain'
  | 'expired'
  | 'refused'
  | 'done'
  | 'not-done';

export type PairingStage = 'checking' | 'confirm' | 'sent' | 'refused';

export type Turn =
  | { readonly kind: 'you'; readonly id: string; readonly rev: number; readonly text: string }
  | { readonly kind: 'say'; readonly id: string; readonly rev: number; readonly text: string; readonly tone: Tone }
  | {
      readonly kind: 'ask';
      readonly id: string;
      readonly rev: number;
      readonly lead: string;
      readonly question: string;
      readonly quote: readonly QuoteLine[];
      readonly options: readonly OptionView[];
      readonly state: AskState;
      readonly answer: string | null;
    }
  | {
      readonly kind: 'consent';
      readonly id: string;
      readonly rev: number;
      readonly sentence: string;
      readonly quote: readonly QuoteLine[];
      readonly grave: boolean;
      readonly state: ConsentState;
      /** Seconds (page clock): Approve works from then on. */
      readonly armedAt: number;
      readonly canApprove: boolean;
      readonly tooLong: boolean;
    }
  | { readonly kind: 'pairing'; readonly id: string; readonly rev: number; readonly stage: PairingStage; readonly code: string | null };

export interface Presence {
  readonly phase: LinkPhase;
  /** A machine reason the page words itself ('not-enrolled', 'another-tab', ...). */
  readonly reason: string | null;
}

export interface Activity {
  readonly running: boolean;
  readonly waiting: boolean;
}

export interface CoreLink {
  readonly presence: Presence;
  readonly activity: Activity;
  /** The question the composer would answer, if one is open and was bound to it. */
  readonly replyTo: string | null;
  turns(): readonly Turn[];
  onChange(cb: () => void): () => void;
  /** ADD_GOAL: what the person typed. */
  say(text: string): void;
  /** Gate 1: an answer chip, or the composer bound to the question. */
  answer(turnId: string, value: string): void;
  /** Gate 2: must run inside the click. */
  approve(turnId: string): Promise<void>;
  cancel(turnId: string): void;
  /** Stop the goal that is running, whoever started it. */
  stop(): void;
  confirmPairing(): Promise<void>;
  cancelPairing(): void;
  /** Attention: hold new cards while the person types or is away; release shows them. */
  hold(on: boolean): void;
  /** The page became visible again: open Gate 2 cards need a fresh look before Approve works. */
  rearm(): void;
  /** Page-clock seconds. */
  now(): number;
}
