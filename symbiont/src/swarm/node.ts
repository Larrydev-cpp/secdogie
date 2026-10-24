/**
 * The contributor node's runtime: do one public task and sign the result the
 * Python core accepts. A node receives a core-signed DISPATCH (a public crawl
 * task), does a bounded fetch and the WASM route scan, and signs a result whose
 * output is shaped exactly like `secdogie_swarm.worker.run_crawl` -- so a crawl
 * of the same page yields the same digest in either language, and consensus can
 * mix TS and Python nodes.
 *
 * Only public work ever happens here: no credential, no inference, no API key.
 */

import { type CanonObject, type CanonValue } from '../core/canon.ts';
import { type Signer } from '../core/ed25519.ts';
import type { GraphEngine, ScanResult } from '../graph/wasm.ts';
import { type FetchLike, type FetchOutcome, boundedFetch } from '../sandbox/bounded_fetch.ts';
import { type FetchPolicy, makePolicy } from '../sandbox/policy.ts';
import { signResult } from './tasks.ts';

type PageOutcome = Extract<FetchOutcome, { kind: 'page' }>;

/** Map a fetched + scanned page to the worker output contract. `offset` is
 * dropped so nodes agree on topology, not byte positions; candidates are sorted
 * by key for a single encoding. */
export function pageOutput(out: PageOutcome, scan: ScanResult): CanonObject {
  const candidates = scan.candidates
    .map(
      (c): CanonObject => ({
        origin: c.origin,
        route: c.route,
        query_keys: [...c.query_keys],
        key: c.key,
        via: c.via,
        method: c.method,
        fields: [...c.fields],
      }),
    )
    .sort((a, b) => ((a['key'] as string) < (b['key'] as string) ? -1 : (a['key'] as string) > (b['key'] as string) ? 1 : 0));
  const doc = scan.document;
  return {
    kind: 'page',
    url: out.url,
    status: out.status,
    content_hash: out.contentHash,
    byte_len: out.byteLen,
    truncated: out.truncated,
    document: doc ? { origin: doc.origin, route: doc.route, query_keys: [...doc.query_keys], key: doc.key } : null,
    candidates,
  };
}

/** Do one crawl task and return its output (unsigned). */
export async function crawl(
  policy: FetchPolicy,
  engine: GraphEngine,
  url: string,
  fetchImpl?: FetchLike,
): Promise<CanonObject> {
  const out = fetchImpl ? await boundedFetch(policy, url, fetchImpl) : await boundedFetch(policy, url);
  if (out.kind !== 'page') {
    const status = 'status' in out ? out.status : 0;
    return {
      kind: out.kind,
      url: out.url,
      status,
      content_hash: '',
      byte_len: 0,
      truncated: false,
      document: null,
      candidates: [],
    };
  }
  const scan = engine.scanRoutes(
    { document_url: out.url, allowed_origins: policy.allowedOrigins, max_bytes: policy.maxBodyBytes },
    out.body,
  );
  return pageOutput(out, scan);
}

/** Verify-and-run a core-signed DISPATCH task, returning the node-signed result.
 * The caller is expected to have already checked the task's signer against a
 * trust set; this builds the policy from the task and does the public work. */
export async function runTask(
  signer: Signer,
  signedTask: CanonObject,
  engine: GraphEngine,
  nowMs: number,
  fetchImpl?: FetchLike,
): Promise<CanonObject> {
  const spec = signedTask['spec'] as CanonObject | undefined;
  if (!spec || typeof spec['url'] !== 'string' || !Array.isArray(spec['allowed_origins'])) {
    throw new Error('not a crawl task');
  }
  const policy = makePolicy({ allowedOrigins: (spec['allowed_origins'] as CanonValue[]).map(String) });
  const output = await crawl(policy, engine, spec['url'], fetchImpl);
  return signResult(signer, signedTask['task_id'] as string, output, nowMs);
}
