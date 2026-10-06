/**
 * What runs inside the sandbox worker, without the worker: a bounded fetch,
 * then the WASM lexical route adapter over the bytes. Kept apart from
 * `worker.ts` so it is tested directly.
 *
 * The result is candidate states and a content hash -- never the page itself
 * leaving the sandbox, never anything executed from it.
 */

import type { GraphEngine, ScanResult } from '../graph/wasm.ts';
import { type FetchLike, type FetchOutcome, boundedFetch } from './bounded_fetch.ts';
import type { FetchPolicy } from './policy.ts';

export type SandboxResult =
  | {
      readonly kind: 'scanned';
      readonly url: string;
      readonly scan: ScanResult;
      readonly contentHash: string;
      readonly byteLen: number;
      readonly truncated: boolean;
    }
  | Exclude<FetchOutcome, { kind: 'page' }>;

export class SandboxCore {
  readonly policy: FetchPolicy;
  readonly #engine: GraphEngine;
  readonly #fetch: FetchLike;

  constructor(policy: FetchPolicy, engine: GraphEngine, fetchImpl: FetchLike = fetch) {
    this.policy = policy;
    this.#engine = engine;
    this.#fetch = fetchImpl;
  }

  async scan(url: string): Promise<SandboxResult> {
    const out = await boundedFetch(this.policy, url, this.#fetch);
    if (out.kind !== 'page') return out;
    const scan = this.#engine.scanRoutes(
      { document_url: out.url, allowed_origins: this.policy.allowedOrigins, max_bytes: this.policy.maxBodyBytes },
      out.body,
    );
    return {
      kind: 'scanned',
      url: out.url,
      scan: { ...scan, truncated: scan.truncated || out.truncated },
      contentHash: out.contentHash,
      byteLen: out.byteLen,
      truncated: out.truncated,
    };
  }
}
