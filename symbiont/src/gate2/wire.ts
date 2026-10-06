/**
 * The operator dialogue wire (`secdogie/dialogue/v1`), the subset this runtime
 * speaks: Socratic dialogue packets and the two Gate 2 packets. Field for
 * field the shapes of `dialogue/secdogie_dialogue/protocol.py`, so a challenge
 * sealed by a Python node opens here and a response sealed here opens there
 * (fixtures/vectors/gate2.json).
 *
 *   {"header": {...}, "kind": "...", "payload": {...}, "signer": did, "sig": b64}
 *
 * {@link openEnvelope} authenticates before it looks inside: signature and
 * trust first, then a strict parse (every field required, unknown fields
 * refused, exact types), then recipient, freshness and replay.
 */

import {
  type CanonObject,
  type CanonValue,
  PyFloat,
  asBigInt,
  isCanonObject,
  pyFloat,
} from '../core/canon.ts';
import type { Signer } from '../core/ed25519.ts';
import { type TrustSet, signPayload, verifyPayload } from '../core/envelope.ts';
import type { TargetAction } from './authz.ts';

export const PROTOCOL_VERSION = 'secdogie/dialogue/v1';
export const DEFAULT_MAX_SKEW_NS = 30n * 1_000_000_000n;
export const DEFAULT_REPLAY_WINDOW = 256;
export const DEFAULT_MAX_SESSIONS = 1024;

export class ProtocolError extends Error {}

export interface Header {
  readonly version: string;
  readonly sender_did: string;
  readonly recipient_did: string;
  readonly session_id: string;
  readonly seq: number;
  readonly timestamp_ns: bigint;
}

export const PacketKind = {
  Dialogue: 'dialogue',
  Gate2Challenge: 'gate2_challenge',
  Gate2Response: 'gate2_response',
} as const;
export type PacketKind = (typeof PacketKind)[keyof typeof PacketKind];

export const DialogueType = {
  SocraticQuestion: 'SocraticQuestion',
  UserClarification: 'UserClarification',
  SystemStatus: 'SystemStatus',
} as const;
export type DialogueType = (typeof DialogueType)[keyof typeof DialogueType];

export interface DialoguePacket {
  readonly probe_id: string;
  readonly dialogue_type: DialogueType;
  readonly content: string;
  readonly in_reply_to: string;
  readonly suggested_options: readonly string[];
  readonly gate_finding: string;
}

export const RiskLevel = { Low: 'low', High: 'high', Irreversible: 'irreversible' } as const;
export type RiskLevel = (typeof RiskLevel)[keyof typeof RiskLevel];

export interface Gate2ChallengePacket {
  readonly challenge_id: string;
  readonly target_action: TargetAction;
  readonly risk_level: RiskLevel;
  readonly risk_explanation: string;
  /** What the node claims; the operator side recomputes and compares. */
  readonly action_hash: string;
  /** The node the authorization would be for. */
  readonly subject_did: string;
  /** Seconds since the epoch (a Python float on the wire). */
  readonly expires_at: number;
}

export const Verdict = { Approve: 'Approve', Deny: 'Deny' } as const;
export type Verdict = (typeof Verdict)[keyof typeof Verdict];

export interface Gate2ResponsePacket {
  readonly challenge_id: string;
  readonly action_hash: string;
  readonly user_verdict: Verdict;
  /** The signed token on Approve; `{}` on Deny. */
  readonly authorization: CanonObject;
}

export type Packet =
  | { readonly kind: typeof PacketKind.Dialogue; readonly packet: DialoguePacket }
  | { readonly kind: typeof PacketKind.Gate2Challenge; readonly packet: Gate2ChallengePacket }
  | { readonly kind: typeof PacketKind.Gate2Response; readonly packet: Gate2ResponsePacket };

export function dialoguePacket(p: Partial<DialoguePacket> & Pick<DialoguePacket, 'probe_id' | 'dialogue_type' | 'content'>): DialoguePacket {
  const pkt: DialoguePacket = {
    probe_id: p.probe_id,
    dialogue_type: p.dialogue_type,
    content: p.content,
    in_reply_to: p.in_reply_to ?? '',
    suggested_options: p.suggested_options ?? [],
    gate_finding: p.gate_finding ?? '',
  };
  checkDialogue(pkt);
  return pkt;
}

function checkDialogue(p: DialoguePacket): void {
  if (p.dialogue_type === DialogueType.UserClarification && !p.in_reply_to) {
    throw new ProtocolError('a clarification must name the probe it answers (in_reply_to)');
  }
  if (p.dialogue_type === DialogueType.SocraticQuestion && !p.probe_id) {
    throw new ProtocolError('a probe needs a probe_id');
  }
}

function checkResponse(p: Gate2ResponsePacket): void {
  const has = Object.keys(p.authorization).length > 0;
  if (p.user_verdict === Verdict.Deny && has) throw new ProtocolError('a Deny carries no authorization');
  if (p.user_verdict === Verdict.Approve && !has) {
    throw new ProtocolError('an Approve must carry the signed authorization');
  }
}

// ---- to the wire ------------------------------------------------------------

function headerWire(h: Header): CanonObject {
  return {
    version: h.version,
    sender_did: h.sender_did,
    recipient_did: h.recipient_did,
    session_id: h.session_id,
    seq: h.seq,
    timestamp_ns: h.timestamp_ns,
  };
}

function actionWire(a: TargetAction): CanonObject {
  return {
    kind: a.kind,
    target_id: a.target_id,
    target_role: a.target_role,
    target_name: a.target_name,
    text: a.text,
    high_risk: a.high_risk,
  };
}

export function packetWire(p: Packet): CanonObject {
  switch (p.kind) {
    case PacketKind.Dialogue: {
      const d = p.packet;
      return {
        probe_id: d.probe_id,
        dialogue_type: d.dialogue_type,
        content: d.content,
        in_reply_to: d.in_reply_to,
        suggested_options: [...d.suggested_options],
        gate_finding: d.gate_finding,
      };
    }
    case PacketKind.Gate2Challenge: {
      const c = p.packet;
      return {
        challenge_id: c.challenge_id,
        target_action: actionWire(c.target_action),
        risk_level: c.risk_level,
        risk_explanation: c.risk_explanation,
        action_hash: c.action_hash,
        subject_did: c.subject_did,
        expires_at: pyFloat(c.expires_at),
      };
    }
    case PacketKind.Gate2Response: {
      const r = p.packet;
      return {
        challenge_id: r.challenge_id,
        action_hash: r.action_hash,
        user_verdict: r.user_verdict,
        authorization: r.authorization,
      };
    }
  }
}

/** Signs `packet` under `header`; the header must name this signer as sender. */
export async function seal(signer: Signer, header: Header, p: Packet): Promise<CanonObject> {
  if (header.version !== PROTOCOL_VERSION) throw new Error(`header.version must be ${PROTOCOL_VERSION}`);
  if (header.sender_did !== signer.did) throw new Error("header.sender_did must be the signing identity's DID");
  return signPayload(signer, { header: headerWire(header), kind: p.kind, payload: packetWire(p) });
}

/** Stamps each packet of one session direction with the next seq and the time. */
export class Sender {
  readonly signer: Signer;
  readonly recipientDid: string;
  readonly sessionId: string;
  readonly #clockNs: () => bigint;
  #seq = 0;

  constructor(signer: Signer, recipientDid: string, opts: { sessionId?: string; clockNs?: () => bigint } = {}) {
    this.signer = signer;
    this.recipientDid = recipientDid;
    this.sessionId = opts.sessionId ?? randomHex(16);
    this.#clockNs = opts.clockNs ?? (() => BigInt(Date.now()) * 1_000_000n);
  }

  seal(p: Packet): Promise<CanonObject> {
    this.#seq += 1;
    const header: Header = {
      version: PROTOCOL_VERSION,
      sender_did: this.signer.did,
      recipient_did: this.recipientDid,
      session_id: this.sessionId,
      seq: this.#seq,
      timestamp_ns: this.#clockNs(),
    };
    return seal(this.signer, header, p);
  }
}

export function randomHex(bytes: number): string {
  const b = crypto.getRandomValues(new Uint8Array(bytes));
  return [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
}

// ---- strict parsing -----------------------------------------------------------

type Field = 'str' | 'int' | 'bigint' | 'float' | 'bool' | 'obj' | 'strs' | readonly string[];

function fieldsOf(obj: CanonValue | undefined, spec: Record<string, Field>, where: string): Record<string, unknown> {
  if (!isCanonObject(obj)) throw new ProtocolError(`${where}: expected an object`);
  const names = Object.keys(spec);
  const unknown = Object.keys(obj).filter((k) => !names.includes(k));
  if (unknown.length) throw new ProtocolError(`${where}: unknown field(s) ${JSON.stringify(unknown.sort())}`);
  const out: Record<string, unknown> = {};
  for (const [name, type] of Object.entries(spec)) {
    if (!Object.hasOwn(obj, name)) throw new ProtocolError(`${where}: missing field '${name}'`);
    out[name] = value(type, obj[name], `${where}.${name}`);
  }
  return out;
}

function value(type: Field, v: CanonValue | undefined, where: string): unknown {
  if (Array.isArray(type)) {
    if (typeof v !== 'string' || !type.includes(v)) throw new ProtocolError(`${where}: unknown value ${JSON.stringify(v)}`);
    return v;
  }
  switch (type) {
    case 'str':
      if (typeof v !== 'string') throw new ProtocolError(`${where}: expected a string`);
      return v;
    case 'bool':
      if (typeof v !== 'boolean') throw new ProtocolError(`${where}: expected a bool`);
      return v;
    case 'int': {
      const n = asBigInt(v);
      if (n === null || n > BigInt(Number.MAX_SAFE_INTEGER)) throw new ProtocolError(`${where}: expected an int`);
      return Number(n);
    }
    case 'bigint': {
      const n = asBigInt(v);
      if (n === null) throw new ProtocolError(`${where}: expected an int`);
      return n;
    }
    case 'float': {
      // Python: type(v) in (int, float) and finite -> float(v)
      if (v instanceof PyFloat) return v.value;
      if (typeof v === 'number') return v;
      if (typeof v === 'bigint') return Number(v);
      throw new ProtocolError(`${where}: expected a finite number`);
    }
    case 'obj':
      if (!isCanonObject(v)) throw new ProtocolError(`${where}: expected an object`);
      return v;
    case 'strs':
      if (!Array.isArray(v) || !v.every((x) => typeof x === 'string')) {
        throw new ProtocolError(`${where}: expected a list of strings`);
      }
      return [...v] as string[];
  }
  throw new ProtocolError(`${where}: unsupported field type`);
}

export function parseHeader(v: CanonValue | undefined): Header {
  const h = fieldsOf(v, {
    version: 'str', sender_did: 'str', recipient_did: 'str', session_id: 'str', seq: 'int', timestamp_ns: 'bigint',
  }, 'header') as unknown as Header;
  if (h.seq < 0 || h.timestamp_ns < 0n) throw new ProtocolError('seq and timestamp_ns must be non-negative');
  return h;
}

function parseAction(v: CanonValue | undefined, where: string): TargetAction {
  return fieldsOf(v, {
    kind: 'str', target_id: 'str', target_role: 'str', target_name: 'str', text: 'str', high_risk: 'bool',
  }, where) as unknown as TargetAction;
}

export function parsePacket(kind: CanonValue | undefined, payload: CanonValue | undefined): Packet {
  switch (kind) {
    case PacketKind.Dialogue: {
      const d = fieldsOf(payload, {
        probe_id: 'str',
        dialogue_type: Object.values(DialogueType),
        content: 'str',
        in_reply_to: 'str',
        suggested_options: 'strs',
        gate_finding: 'str',
      }, 'dialogue') as unknown as DialoguePacket;
      checkDialogue(d);
      return { kind, packet: d };
    }
    case PacketKind.Gate2Challenge: {
      const c = fieldsOf(payload, {
        challenge_id: 'str',
        target_action: 'obj',
        risk_level: Object.values(RiskLevel),
        risk_explanation: 'str',
        action_hash: 'str',
        subject_did: 'str',
        expires_at: 'float',
      }, 'gate2_challenge');
      const packet = { ...c, target_action: parseAction(c['target_action'] as CanonValue, 'gate2_challenge.target_action') };
      return { kind, packet: packet as unknown as Gate2ChallengePacket };
    }
    case PacketKind.Gate2Response: {
      const r = fieldsOf(payload, {
        challenge_id: 'str', action_hash: 'str', user_verdict: Object.values(Verdict), authorization: 'obj',
      }, 'gate2_response') as unknown as Gate2ResponsePacket;
      checkResponse(r);
      return { kind, packet: r };
    }
    default:
      throw new ProtocolError(`kind ${JSON.stringify(kind)} is not spoken by this runtime`);
  }
}

// ---- opening ------------------------------------------------------------------

/** One (sender, session)'s highest seq and the window below it. */
class SeqWindow {
  readonly size: number;
  highest: number | null = null;
  readonly seen = new Set<number>();

  constructor(size: number) {
    this.size = size;
  }

  check(seq: number): string | null {
    if (this.highest === null || seq > this.highest) return null;
    if (this.highest - seq >= this.size) return 'sequence number too old (outside the replay window)';
    if (this.seen.has(seq)) return 'replayed sequence number';
    return null;
  }

  commit(seq: number): void {
    this.seen.add(seq);
    if (this.highest === null || seq > this.highest) {
      this.highest = seq;
      for (const s of this.seen) if (this.highest - s >= this.size) this.seen.delete(s);
    }
  }
}

/** Freshness and replay, as Python's `ReplayGuard`. One per receiving identity. */
export class ReplayGuard {
  readonly #skew: bigint;
  readonly #max: number;
  readonly #window: number;
  readonly #clockNs: () => bigint;
  readonly #sessions = new Map<string, { win: SeqWindow; last: bigint }>();

  constructor(opts: { maxSkewNs?: bigint; maxSessions?: number; window?: number; clockNs?: () => bigint } = {}) {
    this.#skew = opts.maxSkewNs ?? DEFAULT_MAX_SKEW_NS;
    this.#max = opts.maxSessions ?? DEFAULT_MAX_SESSIONS;
    this.#window = opts.window ?? DEFAULT_REPLAY_WINDOW;
    this.#clockNs = opts.clockNs ?? (() => BigInt(Date.now()) * 1_000_000n);
  }

  admit(h: Header): string | null {
    const now = this.#clockNs();
    const d = now - h.timestamp_ns;
    if ((d < 0n ? -d : d) > this.#skew) return 'stale or future-dated packet (outside the clock-skew window)';
    const key = `${h.sender_did}\u0000${h.session_id}`;
    let entry = this.#sessions.get(key);
    if (entry === undefined) {
      if (this.#sessions.size >= this.#max) {
        for (const [k, e] of this.#sessions) if (now - e.last > 2n * this.#skew) this.#sessions.delete(k);
        if (this.#sessions.size >= this.#max) return 'too many live sessions';
      }
      entry = { win: new SeqWindow(this.#window), last: now };
    }
    const reason = entry.win.check(h.seq);
    if (reason) return reason;
    entry.win.commit(h.seq);
    entry.last = now;
    this.#sessions.set(key, entry);
    return null;
  }
}

export type Opened =
  | { readonly ok: true; readonly header: Header; readonly signer: string; readonly packet: Packet }
  | { readonly ok: false; readonly reason: string; readonly signer: string | null };

/** Authenticate, then parse, then admit -- the order of Python's `open_envelope`. */
export async function openEnvelope(
  obj: unknown,
  opts: { trust: TrustSet; selfDid: string; replay: ReplayGuard },
): Promise<Opened> {
  if (!isCanonObject(obj)) return { ok: false, reason: 'not an object', signer: null };
  const { ok, signer } = await verifyPayload(obj, opts.trust);
  if (!ok) {
    return { ok: false, reason: signer ? 'sender not trusted or revoked' : 'invalid or missing signature', signer };
  }
  const keys = Object.keys(obj).filter((k) => k !== 'signer' && k !== 'sig').sort();
  if (keys.join(',') !== 'header,kind,payload') return { ok: false, reason: 'not a dialogue envelope', signer };
  let header: Header;
  let packet: Packet;
  try {
    header = parseHeader(obj['header']);
  } catch (e) {
    return { ok: false, reason: `bad header: ${(e as Error).message}`, signer };
  }
  if (header.version !== PROTOCOL_VERSION) return { ok: false, reason: 'wrong protocol version (domain separation)', signer };
  if (header.sender_did !== signer) return { ok: false, reason: 'header sender is not the signer', signer };
  if (header.recipient_did !== opts.selfDid) return { ok: false, reason: 'addressed to a different node', signer };
  try {
    packet = parsePacket(obj['kind'], obj['payload']);
  } catch (e) {
    return { ok: false, reason: `bad payload: ${(e as Error).message}`, signer };
  }
  const reason = opts.replay.admit(header);
  if (reason) return { ok: false, reason, signer };
  return { ok: true, header, signer: signer!, packet };
}
