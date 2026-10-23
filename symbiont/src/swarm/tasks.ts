/**
 * Swarm task/result helpers, mirroring `secdogie_swarm.tasks`: the digest
 * consensus agrees on, and producing a node-signed result.
 *
 * A contributor node verifies the core-signed task it is handed, does the public
 * work (bounded fetch + route scan -- the symbiont sandbox / graph wasm), and
 * signs a result the Python core accepts. Timestamps are integer milliseconds,
 * so nothing here ever needs a float.
 */

import { type CanonObject, canonicalBytes, sha256Hex } from '../core/canon.ts';
import { type Signer } from '../core/ed25519.ts';
import { type TrustSet, signPayload, verifyPayload } from '../core/envelope.ts';

export const PROTOCOL = 'secdogie/swarm/v1';

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
