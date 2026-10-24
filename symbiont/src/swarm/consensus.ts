/**
 * Untrusted-node results, mirroring `secdogie_swarm.consensus`: a result counts
 * only when it is signed by the node it was assigned to and agreed by a quorum;
 * other digests are outliers and dropped. This is the Workers core's belief
 * logic, byte-compatible with the Python core.
 */

import { isCanonObject } from '../core/canon.ts';
import { type TrustSet, verifyPayload } from '../core/envelope.ts';
import { PROTOCOL, outputDigest } from './tasks.ts';

export interface VerifyResult {
  readonly ok: boolean;
  readonly reason: string | null;
  readonly nodeDid: string | null;
  readonly digest: string | null;
}

export async function verifyResult(
  result: unknown,
  taskId: string,
  assigned: ReadonlySet<string>,
  trust: TrustSet,
): Promise<VerifyResult> {
  const no = (reason: string, nodeDid: string | null = null): VerifyResult => ({ ok: false, reason, nodeDid, digest: null });
  if (!isCanonObject(result)) return no('not an object');
  if (result['protocol'] !== PROTOCOL) return no('wrong protocol');
  const v = await verifyPayload(result, trust);
  if (!v.ok) return no('signature not valid or node not enrolled', v.signer);
  const signer = v.signer as string;
  if (result['node_did'] !== signer) return no('node_did does not match signer', signer);
  if (result['task_id'] !== taskId) return no('result is for another task', signer);
  if (!assigned.has(signer)) return no('node was not assigned this task', signer);
  const output = result['output'];
  if (!isCanonObject(output)) return no('missing output', signer);
  return { ok: true, reason: null, nodeDid: signer, digest: await outputDigest(output) };
}

export class Tally {
  readonly votes = new Map<string, Set<string>>();

  add(nodeDid: string, digest: string): void {
    const set = this.votes.get(digest) ?? new Set<string>();
    set.add(nodeDid);
    this.votes.set(digest, set);
  }

  /** The digest that reached `quorum` agreeing nodes, with those nodes, or null. */
  decide(quorum: number): { digest: string; agree: ReadonlySet<string> } | null {
    let best: { digest: string; agree: Set<string> } | null = null;
    for (const [digest, nodes] of this.votes) {
      if (nodes.size >= quorum && (best === null || nodes.size > best.agree.size)) best = { digest, agree: nodes };
    }
    return best;
  }

  outliers(winningDigest: string): ReadonlySet<string> {
    const out = new Set<string>();
    for (const [digest, nodes] of this.votes) if (digest !== winningDigest) for (const n of nodes) out.add(n);
    return out;
  }
}

/** Default quorum: a strict majority of the assigned copies. */
export const quorumFor = (redundancy: number): number => Math.floor(redundancy / 2) + 1;
