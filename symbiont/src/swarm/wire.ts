/**
 * The core <-> node wire messages, mirroring `secdogie_swarm.wire`. Canonical
 * JSON, so a message a Python core signs or sends is byte-identical to one this
 * TypeScript node produces. A node only ever receives public work: a DISPATCH
 * carries a public task and a public checkpoint, never a credential.
 *
 * `decode` is strict (via the lossless parser): a wrong protocol, an unknown
 * type, NaN/Infinity and duplicate keys are all refused.
 */

import { type CanonObject, type CanonValue, canonicalBytes, isCanonObject, parseLossless } from '../core/canon.ts';

export const WIRE_PROTOCOL = 'secdogie/swarm-wire/v1';

export const DISPATCH = 'DISPATCH';
export const RESULT = 'RESULT';
export const HEARTBEAT = 'HEARTBEAT';
export const PROBE = 'PROBE';
export const PONG = 'PONG';
export const CHECKPOINT = 'CHECKPOINT';
export const CANCEL = 'CANCEL';

const REQUIRED: Record<string, readonly string[]> = {
  [DISPATCH]: ['task'],
  [RESULT]: ['result'],
  [HEARTBEAT]: ['node_did'],
  [PROBE]: ['nonce', 'sent_at_ms'],
  [PONG]: ['node_did', 'nonce'],
  [CHECKPOINT]: ['task_id', 'node_did', 'checkpoint'],
  [CANCEL]: ['task_id', 'reason'],
};

export class WireError extends Error {}

const msg = (type: string, fields: Record<string, CanonValue>): CanonObject => ({ v: WIRE_PROTOCOL, type, ...fields });

export const dispatch = (task: CanonObject, resumeFrom?: CanonObject): CanonObject =>
  resumeFrom === undefined ? msg(DISPATCH, { task }) : msg(DISPATCH, { task, resume_from: resumeFrom });
export const result = (signedResult: CanonObject): CanonObject => msg(RESULT, { result: signedResult });
export const heartbeat = (nodeDid: string): CanonObject => msg(HEARTBEAT, { node_did: nodeDid });
export const probe = (nonce: string, sentAtMs: number): CanonObject => msg(PROBE, { nonce, sent_at_ms: sentAtMs });
export const pong = (nodeDid: string, nonce: string): CanonObject => msg(PONG, { node_did: nodeDid, nonce });
export const checkpoint = (taskId: string, nodeDid: string, blob: CanonObject): CanonObject =>
  msg(CHECKPOINT, { task_id: taskId, node_did: nodeDid, checkpoint: blob });
export const cancel = (taskId: string, reason: string): CanonObject => msg(CANCEL, { task_id: taskId, reason });

export function validate(m: unknown): asserts m is CanonObject {
  if (!isCanonObject(m)) throw new WireError('message must be an object');
  if (m['v'] !== WIRE_PROTOCOL) throw new WireError('wrong or missing wire protocol');
  const t = m['type'];
  if (typeof t !== 'string' || !(t in REQUIRED)) throw new WireError(`unknown message type ${String(t)}`);
  for (const field of REQUIRED[t]!) {
    if (!Object.hasOwn(m, field)) throw new WireError(`${t} missing ${field}`);
  }
}

export function encode(m: CanonObject): Uint8Array<ArrayBuffer> {
  validate(m);
  return canonicalBytes(m);
}

export function decode(data: Uint8Array | string): CanonObject {
  const text = typeof data === 'string' ? data : new TextDecoder().decode(data);
  let v: CanonValue;
  try {
    v = parseLossless(text);
  } catch (e) {
    throw new WireError(`not valid JSON: ${(e as Error).message}`);
  }
  validate(v);
  return v;
}
