/**
 * A node's Socratic question as a gentle card: the finding that raised it said
 * in plain words, the question itself, and the answers as chips. The loop's own
 * confirmation prompts ("approve plan: ...", "Execute key(delete)?") are
 * recognized and reworded; anything else is the model's question, shown as it
 * was asked. The value sent back is always exactly what the node offered
 * ("Approve" stays "Approve" on the wire, whatever the chip says).
 */

import type { DialoguePacket } from '../gate2/wire.ts';
import { type QuoteLine, keysLabel } from './consent.ts';
import { type Dict, FINDING_CODES, type FindingCode } from './dict.ts';
import { en } from './en.ts';
import { visible } from './visible.ts';

export interface OptionView {
  readonly label: string;
  readonly value: string;
}

export interface AskView {
  readonly lead: string;
  readonly question: string;
  readonly quote: readonly QuoteLine[];
  readonly options: readonly OptionView[];
}

const PLAN = /^approve plan: ([\s\S]*)$/;
const STEP = /^Execute (HIGH-RISK )?([\w.-]+)\(([\s\S]*)\)\?$/;

function isFinding(code: string): code is FindingCode {
  return (FINDING_CODES as readonly string[]).includes(code);
}

/** The finding in plain words (English when this language lacks it; '' when unknown). */
export function findingLine(d: Dict, code: string): string {
  if (!code || !isFinding(code)) return '';
  return d.ask.finding[code] ?? en.ask.finding[code] ?? '';
}

function stepAction(d: Dict, kind: string, raw: string): string {
  const k = kind.toLowerCase();
  if (k === 'key' || k === 'hotkey') return d.action.key(visible(keysLabel(raw)));
  if (k === 'type') return d.action.typeBlind;
  if (k.includes('click')) return d.action.clickBlind;
  return d.action.other(visible(kind), '');
}

export function askView(d: Dict, p: DialoguePacket): AskView {
  const lead = findingLine(d, p.gate_finding);
  const quote: QuoteLine[] = [];
  let question: string;
  const plan = PLAN.exec(p.content);
  const step = STEP.exec(p.content);
  if (plan) {
    question = d.ask.confirmPlan;
    if (plan[1]) quote.push({ label: d.consent.quoteLabel.text, text: visible(plan[1]) });
  } else if (step) {
    question = d.ask.confirmStep(stepAction(d, step[2]!, step[3]!));
    if (step[2]!.toLowerCase() === 'type' && step[3]) quote.push({ label: d.consent.quoteLabel.text, text: visible(step[3]) });
  } else if (p.content === 'Allow the agent to continue?') {
    question = d.ask.continueAsk;
  } else {
    question = visible(p.content);
  }
  const options = p.suggested_options.map((o) =>
    o === 'Approve' ? { label: d.ask.yes, value: o } : o === 'Deny' ? { label: d.ask.no, value: o } : { label: visible(o), value: o },
  );
  return { lead, question: question === lead ? '' : question, quote, options };
}
