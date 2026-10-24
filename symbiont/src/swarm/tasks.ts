/**
 * Swarm task/result helpers, mirroring `secdogie_swarm.tasks`: the digest
 * consensus agrees on, and producing a node-signed result.
 *
 * A contributor node verifies the core-signed task it is handed, does the public
 * work (bounded fetch + route scan -- the symbiont sandbox / graph wasm), and
 * signs a result the Python core accepts. Timestamps are integer milliseconds,
 * so nothing here ever needs a float.
 */

import { type CanonObject, type CanonValue, canonicalBytes, sha256Hex } from '../core/canon.ts';
import { type Signer } from '../core/ed25519.ts';
import { type TrustSet, signPayload, verifyPayload } from '../core/envelope.ts';

export const PROTOCOL = 'secdogie/swarm/v1';
export const TASK_KINDS = new Set(['crawl']);
const SPEC_FIELDS: Record<string, readonly string[]> = { crawl: ['url', 'allowed_origins'] };
const SECRET_HINTS = ['key', 'token', 'secret', 'cookie', 'auth', 'password', 'credential', 'session', 'bearer', 'private', 'apikey', 'api_key'];

export class TaskError extends Error {}

const secretShaped = (name: string): boolean => {
  const low = name.toLowerCase();
  return SECRET_HINTS.some((h) => low.includes(h));
};

/** Refuse unless the task carries public work only -- no secret-shaped key,
 * nested too, outside the signature envelope. Mirrors `assert_public`. */
export function assertPublic(task: CanonObject): void {
  const walk = (node: CanonValue, path: string): void => {
    if (node !== null && typeof node === 'object' && !Array.isArray(node)) {
      for (const [k, v] of Object.entries(node)) {
        if (secretShaped(k)) throw new TaskError(`secret-shaped key ${k} at ${path} -- tasks carry public work only`);
        walk(v, `${path}.${k}`);
      }
    } else if (Array.isArray(node)) {
      node.forEach((v, i) => walk(v, `${path}[${i}]`));
    }
  };
  for (const [k, v] of Object.entries(task)) if (k !== 'signer' && k !== 'sig') walk(v, `task.${k}`);
}

const isInt = (v: CanonValue): boolean => typeof v === 'bigint' || (typeof v === 'number' && Number.isInteger(v));

export function validateTask(task: unknown): asserts task is CanonObject {
  if (task === null || typeof task !== 'object' || Array.isArray(task)) throw new TaskError('task must be an object');
  const t = task as CanonObject;
  if (t['protocol'] !== PROTOCOL) throw new TaskError('wrong or missing protocol');
  const kind = t['kind'];
  if (typeof kind !== 'string' || !TASK_KINDS.has(kind)) throw new TaskError(`unknown task kind ${String(kind)}`);
  for (const f of ['task_id', 'issued_at_ms', 'deadline_ms', 'redundancy', 'epoch']) if (!(f in t)) throw new TaskError(`missing ${f}`);
  if (typeof t['task_id'] !== 'string' || t['task_id'] === '') throw new TaskError('task_id must be a non-empty string');
  for (const f of ['issued_at_ms', 'deadline_ms', 'redundancy', 'epoch']) if (!isInt(t[f] as CanonValue)) throw new TaskError(`${f} must be an integer`);
  const spec = t['spec'];
  const want = SPEC_FIELDS[kind]!;
  if (spec === null || typeof spec !== 'object' || Array.isArray(spec)) throw new TaskError('spec must be an object');
  const keys = Object.keys(spec);
  if (keys.length !== want.length || !want.every((k) => k in spec)) throw new TaskError(`spec for ${kind} must be exactly ${want.join(',')}`);
}

export function buildCrawl(p: {
  taskId: string;
  url: string;
  allowedOrigins: string[];
  issuedAtMs: number;
  deadlineMs: number;
  redundancy: number;
  epoch: number;
}): CanonObject {
  if (p.redundancy < 1) throw new TaskError('redundancy must be >= 1');
  const task: CanonObject = {
    protocol: PROTOCOL,
    task_id: p.taskId,
    kind: 'crawl',
    spec: { url: p.url, allowed_origins: [...p.allowedOrigins] },
    issued_at_ms: p.issuedAtMs,
    deadline_ms: p.deadlineMs,
    redundancy: p.redundancy,
    epoch: p.epoch,
  };
  validateTask(task);
  assertPublic(task);
  return task;
}

export function signTask(signer: Signer, task: CanonObject): Promise<CanonObject> {
  validateTask(task);
  assertPublic(task);
  return signPayload(signer, task);
}

/** The canonical hash of a result's output -- what consensus agrees on. */
export function outputDigest(output: CanonObject): Promise<string> {
  return sha256Hex(canonicalBytes(output));
}

/** Verify a core-signed task (and, with a trust set, that the core is trusted). */
export function verifyTask(signedTask: unknown, trust?: TrustSet) {
  return verifyPayload(signedTask, trust);
}

/** Verify a node-signed result. */
export function verifyResult(signedResult: unknown, trust?: TrustSet) {
  return verifyPayload(signedResult, trust);
}

export function buildResult(taskId: string, nodeDid: string, output: CanonObject, producedAtMs: number): CanonObject {
  return { protocol: PROTOCOL, task_id: taskId, node_did: nodeDid, output, produced_at_ms: producedAtMs };
}

/** Sign a result for a task. The signature is over the same canonical bytes the
 * Python core verifies. */
export function signResult(
  signer: Signer,
  taskId: string,
  output: CanonObject,
  producedAtMs: number,
): Promise<CanonObject> {
  return signPayload(signer, buildResult(taskId, signer.did, output, producedAtMs));
}
