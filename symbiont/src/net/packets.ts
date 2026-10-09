/**
 * The packets inside the browser link's records -- the twin of
 * `dialogue/secdogie_dialogue/symbiont_packets.py`: the same fields, limits
 * (in code points), rules, and the same `path` / `rule` for every refusal
 * (fixtures/vectors/symbiont_link.json). A packet is acted on only if it is
 * exactly the canonical encoding of what it parses to.
 *
 * The spec's packets (`ADD_GOAL`, `GATE_1_PROBE`, `GATE_2_CHALLENGE`) keep all
 * their fields; the additions are what the action-authorization/v1 contract
 * and plain-language wording need. A refusal names the field and the rule,
 * never the value.
 */

import {
  type CanonObject,
  type CanonValue,
  PyFloat,
  canonicalText,
  compareCodePoints,
  isCanonObject,
  parseLossless,
} from '../core/canon.ts';

export const MAX_PACKET_BYTES = 65511;
export const DESTRUCTIVE_UNBIND = 'DESTRUCTIVE_UNBIND';
export const AUTHORIZATION_TYPE = 'secdogie/action-authorization/v1';
export const TOKEN_FIELDS = ['action_hash', 'expires_at', 'sig', 'signer', 'subject', 'type', 'valid_from'] as const;
export const CONFIRM_ABOUTS = ['step', 'plan', 'continue'] as const;
export const CONFIRM_IDS = ['approve', 'deny'] as const;

export const LIMITS = {
  intent: 2000,
  reply_text: 2000,
  prompt: 500,
  label: 80,
  candidates: 8,
  emotional_memory: 160,
  target_summary: 200,
  'action.kind': 40,
  'action.target_id': 200,
  'action.target_role': 80,
  'action.target_name': 2000,
  'action.text': 16000,
  'step.keys': 8,
  'step.key': 40,
  'step.text': 2000,
  'step.path': 1000,
  did: 200,
  sig: 200,
} as const;

export const ID = /^[a-z0-9-]{1,40}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const ACTION_TYPE = /^[A-Z_]{1,40}$/;
const STEP_KIND = /^[a-z_]{1,40}$/;
const CONTROL = /\p{Cc}/u;

export const PAGE_TO_NODE = ['ADD_GOAL', 'GATE_1_REPLY', 'GATE_2_REPLY', 'STOP'] as const;
export const NODE_TO_PAGE = ['CURRENT_STATUS', 'GOAL_UPDATE', 'GATE_1_PROBE', 'GATE_2_CHALLENGE', 'GATE_CLOSED'] as const;
export type Direction = 'to-node' | 'to-page';

export class PacketError extends Error {
  readonly path: string;
  readonly rule: string;
  constructor(path: string, rule: string) {
    super(`${path}: ${rule}`);
    this.path = path;
    this.rule = rule;
  }
}

// ---- typed views of the packets ------------------------------------------------------

export interface Candidate {
  readonly id: string;
  readonly label: string;
}
export interface Step {
  readonly kind: string;
  readonly keys?: readonly string[];
  readonly text?: string;
  readonly path?: string;
}
export interface Action {
  readonly kind: string;
  readonly target_id: string;
  readonly target_role: string;
  readonly target_name: string;
  readonly text: string;
  readonly high_risk: boolean;
}
export type About = 'step' | 'plan' | 'continue' | 'question' | 'other';

export interface Gate1Probe {
  readonly type: 'GATE_1_PROBE';
  readonly probe_id: string;
  readonly prompt: string;
  readonly candidates: readonly Candidate[];
  readonly about: About;
  readonly step?: Step;
  readonly goal_id?: string;
  readonly expires_at: number;
}
export interface Gate2Challenge {
  readonly type: 'GATE_2_CHALLENGE';
  readonly challenge_id: string;
  readonly emotional_memory: string;
  readonly target_summary: string;
  readonly action_type: string;
  readonly action: Action;
  readonly action_hash: string;
  readonly subject: string;
  readonly expires_at: number;
  readonly goal_id?: string;
}
export interface CurrentStatus {
  readonly type: 'CURRENT_STATUS';
  readonly status: 'idle' | 'running' | 'waiting';
  readonly goal_id: string;
}
export interface GoalUpdate {
  readonly type: 'GOAL_UPDATE';
  readonly goal_id: string;
  readonly state: 'accepted' | 'refused' | 'done' | 'failed' | 'stopped' | 'paused';
}
export interface GateClosed {
  readonly type: 'GATE_CLOSED';
  readonly kind: 'probe' | 'challenge';
  readonly id: string;
  readonly reason: 'answered' | 'expired';
}
export type NodePacket = Gate1Probe | Gate2Challenge | CurrentStatus | GoalUpdate | GateClosed;

// ---- field checks ----------------------------------------------------------------------

const at = (path: string, key: string) => (path ? `${path}.${key}` : key);
const codePoints = (s: string) => [...s].length;

function fields(obj: unknown, path: string, required: readonly string[], optional: readonly string[] = []): CanonObject {
  if (!isCanonObject(obj) || Array.isArray(obj)) throw new PacketError(path, 'not an object');
  const keys = Object.keys(obj);
  const missing = required.filter((k) => !keys.includes(k)).sort(compareCodePoints);
  if (missing.length) throw new PacketError(at(path, missing[0]!), 'missing');
  const extra = keys.filter((k) => !required.includes(k) && !optional.includes(k)).sort(compareCodePoints);
  if (extra.length) throw new PacketError(at(path, extra[0]!), 'unexpected field');
  return obj;
}

function str(v: CanonValue | undefined, path: string, maxLen: number, minLen = 0, singleLine = false): string {
  if (typeof v !== 'string') throw new PacketError(path, 'not a string');
  const n = codePoints(v);
  if (n < minLen || n > maxLen) throw new PacketError(path, `length outside ${minLen}..${maxLen}`);
  if (singleLine && CONTROL.test(v)) throw new PacketError(path, 'control characters');
  return v;
}

function id(v: CanonValue | undefined, path: string): string {
  if (typeof v !== 'string' || !ID.test(v)) throw new PacketError(path, 'not an id');
  return v;
}

function oneOf<T extends string>(v: CanonValue | undefined, path: string, allowed: readonly T[]): T {
  if (typeof v !== 'string' || !(allowed as readonly string[]).includes(v)) throw new PacketError(path, 'not an allowed value');
  return v as T;
}

function num(v: CanonValue | undefined, path: string): number {
  if (v instanceof PyFloat) return v.value;
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'bigint') return Number(v);
  throw new PacketError(path, 'not a number');
}

function bool(v: CanonValue | undefined, path: string): boolean {
  if (typeof v !== 'boolean') throw new PacketError(path, 'not a boolean');
  return v;
}

function checkToken(tok: CanonValue | undefined, path: string): void {
  const t = fields(tok, path, TOKEN_FIELDS);
  if (t['type'] !== AUTHORIZATION_TYPE) throw new PacketError(`${path}.type`, 'not an action authorization');
  const h = t['action_hash'];
  if (typeof h !== 'string' || !HEX64.test(h)) throw new PacketError(`${path}.action_hash`, 'not a hash');
  str(t['subject'], `${path}.subject`, LIMITS.did, 1);
  str(t['signer'], `${path}.signer`, LIMITS.did, 1);
  str(t['sig'], `${path}.sig`, LIMITS.sig, 1);
  num(t['valid_from'], `${path}.valid_from`);
  num(t['expires_at'], `${path}.expires_at`);
}

function checkStep(step: CanonValue | undefined, path: string): void {
  const s = fields(step, path, ['kind'], ['keys', 'text', 'path']);
  const kind = s['kind'];
  if (typeof kind !== 'string' || !STEP_KIND.test(kind)) throw new PacketError(`${path}.kind`, 'not a step kind');
  if ('keys' in s) {
    const keys = s['keys'];
    if (!Array.isArray(keys) || keys.length < 1 || keys.length > LIMITS['step.keys']) {
      throw new PacketError(`${path}.keys`, 'not a short list');
    }
    (keys as readonly CanonValue[]).forEach((k, i) => str(k, `${path}.keys[${i}]`, LIMITS['step.key'], 1, true));
  }
  if ('text' in s) str(s['text'], `${path}.text`, LIMITS['step.text']);
  if ('path' in s) str(s['path'], `${path}.path`, LIMITS['step.path'], 1);
}

function checkAction(a: CanonValue | undefined, path: string): void {
  const o = fields(a, path, ['kind', 'target_id', 'target_role', 'target_name', 'text', 'high_risk']);
  for (const k of ['kind', 'target_id', 'target_role', 'target_name', 'text'] as const) {
    str(o[k], `${path}.${k}`, LIMITS[`action.${k}`], k === 'kind' ? 1 : 0);
  }
  bool(o['high_risk'], `${path}.high_risk`);
}

const CHECKS: Record<string, (p: CanonObject) => void> = {
  ADD_GOAL(p) {
    fields(p, '', ['type', 'goal_id', 'intent']);
    id(p['goal_id'], 'goal_id');
    str(p['intent'], 'intent', LIMITS.intent, 1);
  },
  GATE_1_REPLY(p) {
    const hasC = 'candidate_id' in p;
    const hasT = 'text' in p;
    if (hasC === hasT) throw new PacketError('candidate_id', 'exactly one of candidate_id or text');
    fields(p, '', ['type', 'probe_id', hasC ? 'candidate_id' : 'text']);
    id(p['probe_id'], 'probe_id');
    if (hasC) id(p['candidate_id'], 'candidate_id');
    else str(p['text'], 'text', LIMITS.reply_text, 1);
  },
  GATE_2_REPLY(p) {
    const decision = p['decision'];
    if (decision === 'approve') {
      fields(p, '', ['type', 'challenge_id', 'decision', 'authorization']);
      checkToken(p['authorization'], 'authorization');
    } else if (decision === 'cancel') {
      fields(p, '', ['type', 'challenge_id', 'decision']);
    } else {
      throw new PacketError('decision', 'not an allowed value');
    }
    id(p['challenge_id'], 'challenge_id');
  },
  STOP(p) {
    fields(p, '', ['type', 'goal_id']);
    id(p['goal_id'], 'goal_id');
  },
  CURRENT_STATUS(p) {
    fields(p, '', ['type', 'status', 'goal_id']);
    const status = oneOf(p['status'], 'status', ['idle', 'running', 'waiting']);
    if (status === 'idle' && p['goal_id'] === '') return;
    id(p['goal_id'], 'goal_id');
  },
  GOAL_UPDATE(p) {
    fields(p, '', ['type', 'goal_id', 'state']);
    id(p['goal_id'], 'goal_id');
    oneOf(p['state'], 'state', ['accepted', 'refused', 'done', 'failed', 'stopped', 'paused']);
  },
  GATE_1_PROBE(p) {
    fields(p, '', ['type', 'probe_id', 'prompt', 'candidates', 'about', 'expires_at'], ['step', 'goal_id']);
    id(p['probe_id'], 'probe_id');
    str(p['prompt'], 'prompt', LIMITS.prompt);
    const about = oneOf(p['about'], 'about', ['step', 'plan', 'continue', 'question', 'other']);
    num(p['expires_at'], 'expires_at');
    if ('goal_id' in p) id(p['goal_id'], 'goal_id');
    if ('step' in p) {
      if (about !== 'step') throw new PacketError('step', 'only for a step');
      checkStep(p['step'], 'step');
    }
    const cands = p['candidates'];
    if (!Array.isArray(cands) || cands.length > LIMITS.candidates) throw new PacketError('candidates', 'not a short list');
    const seen: string[] = [];
    (cands as readonly CanonValue[]).forEach((c, i) => {
      const o = fields(c, `candidates[${i}]`, ['id', 'label']);
      const cid = id(o['id'], `candidates[${i}].id`);
      str(o['label'], `candidates[${i}].label`, LIMITS.label, 1, true);
      if (seen.includes(cid)) throw new PacketError(`candidates[${i}].id`, 'repeated');
      seen.push(cid);
    });
    if ((CONFIRM_ABOUTS as readonly string[]).includes(about) && seen.join(',') !== CONFIRM_IDS.join(',')) {
      throw new PacketError('candidates', 'a confirmation offers approve and deny');
    }
  },
  GATE_2_CHALLENGE(p) {
    fields(
      p,
      '',
      ['type', 'challenge_id', 'emotional_memory', 'target_summary', 'action_type', 'action', 'action_hash', 'subject', 'expires_at'],
      ['goal_id'],
    );
    id(p['challenge_id'], 'challenge_id');
    str(p['emotional_memory'], 'emotional_memory', LIMITS.emotional_memory, 1, true);
    str(p['target_summary'], 'target_summary', LIMITS.target_summary);
    const at2 = p['action_type'];
    if (typeof at2 !== 'string' || !ACTION_TYPE.test(at2)) throw new PacketError('action_type', 'not an action type');
    checkAction(p['action'], 'action');
    const h = p['action_hash'];
    if (typeof h !== 'string' || !HEX64.test(h)) throw new PacketError('action_hash', 'not a hash');
    str(p['subject'], 'subject', LIMITS.did, 1);
    num(p['expires_at'], 'expires_at');
    if ('goal_id' in p) id(p['goal_id'], 'goal_id');
  },
  GATE_CLOSED(p) {
    fields(p, '', ['type', 'kind', 'id', 'reason']);
    oneOf(p['kind'], 'kind', ['probe', 'challenge']);
    id(p['id'], 'id');
    oneOf(p['reason'], 'reason', ['answered', 'expired']);
  },
};

/** Throws {@link PacketError} unless `packet` is well formed (for `direction`). */
export function validate(packet: unknown, direction?: Direction): CanonObject {
  if (!isCanonObject(packet) || Array.isArray(packet)) throw new PacketError('', 'not an object');
  const t = packet['type'];
  if (typeof t !== 'string' || !Object.hasOwn(CHECKS, t)) throw new PacketError('type', 'unknown packet type');
  if ((direction === 'to-node' && !(PAGE_TO_NODE as readonly string[]).includes(t)) ||
      (direction === 'to-page' && !(NODE_TO_PAGE as readonly string[]).includes(t))) {
    throw new PacketError('type', 'wrong direction');
  }
  CHECKS[t]!(packet);
  return packet;
}

/** Validate, then the canonical UTF-8 bytes. A packet this side would refuse is never sent. */
export function encode(packet: CanonObject, direction?: Direction): Uint8Array<ArrayBuffer> {
  validate(packet, direction);
  const bytes = new TextEncoder().encode(canonicalText(packet));
  if (bytes.length > MAX_PACKET_BYTES) throw new PacketError('', 'too large');
  return bytes;
}

/** Parse and validate one record's plaintext; it must be canonical. */
export function decode(data: Uint8Array, direction?: Direction): CanonObject {
  if (data.length > MAX_PACKET_BYTES) throw new PacketError('', 'too large');
  let text: string;
  let obj: CanonValue;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(data);
    obj = parseLossless(text);
  } catch {
    throw new PacketError('', 'not JSON');
  }
  validate(obj, direction);
  if (canonicalText(obj) !== text) throw new PacketError('', 'not canonical');
  return obj as CanonObject;
}

/** A node packet as its typed view (after {@link decode}); `expires_at` as a plain number. */
export function asNodePacket(p: CanonObject): NodePacket {
  const plain = (v: CanonValue): unknown => {
    if (v instanceof PyFloat) return v.value;
    if (typeof v === 'bigint') return Number(v);
    if (Array.isArray(v)) return (v as readonly CanonValue[]).map(plain);
    if (isCanonObject(v)) return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, plain(x)]));
    return v;
  };
  return plain(p) as NodePacket;
}
