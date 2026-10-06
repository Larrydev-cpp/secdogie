/**
 * One bounded, credential-free, non-following GET. See `policy.ts` for the
 * boundary. A cross-origin site that does not allow being read (CORS) fails
 * here and is reported as such -- the sandbox does not route around it.
 */

import { sha256Hex } from '../core/canon.ts';
import { type FetchPolicy, HTML_TYPES, admitUrl } from './policy.ts';

export type FetchOutcome =
  | {
      readonly kind: 'page';
      readonly url: string;
      readonly status: number;
      readonly contentType: string;
      readonly body: Uint8Array<ArrayBuffer>;
      readonly byteLen: number;
      readonly contentHash: string;
      readonly truncated: boolean;
    }
  | {
      readonly kind: 'redirect';
      readonly url: string;
      readonly status: number;
      /** The target, only when readable *and* itself admissible. Never followed. */
      readonly location: string | null;
    }
  | { readonly kind: 'refused'; readonly url: string; readonly reason: string }
  | { readonly kind: 'failed'; readonly url: string; readonly reason: string };

export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

/** Exactly what goes on the wire, and nothing else. */
export function requestInit(signal: AbortSignal): RequestInit {
  return {
    method: 'GET',
    credentials: 'omit',
    redirect: 'manual',
    referrerPolicy: 'no-referrer',
    cache: 'no-store',
    mode: 'cors',
    headers: { Accept: 'text/html, application/xhtml+xml;q=0.9' },
    signal,
  };
}

async function readCapped(
  res: Response,
  max: number,
): Promise<{ body: Uint8Array<ArrayBuffer>; truncated: boolean }> {
  if (!res.body) return { body: new Uint8Array(0), truncated: false };
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let truncated = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const room = max - total;
    if (value.length > room) {
      chunks.push(value.subarray(0, room));
      total += room;
      truncated = true;
      await reader.cancel();
      break;
    }
    chunks.push(value);
    total += value.length;
  }
  const body = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    body.set(c, off);
    off += c.length;
  }
  return { body, truncated };
}

export async function boundedFetch(policy: FetchPolicy, raw: string, fetchImpl: FetchLike = fetch): Promise<FetchOutcome> {
  const admitted = admitUrl(policy, raw);
  if (!admitted.ok) return { kind: 'refused', url: raw, reason: admitted.reason };
  const url = admitted.url.href;
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(new DOMException('timed out', 'TimeoutError')), policy.timeoutMs);
  try {
    let res: Response;
    try {
      res = await fetchImpl(url, requestInit(ac.signal));
    } catch (e) {
      const timedOut = ac.signal.aborted;
      return { kind: 'failed', url, reason: timedOut ? 'timed out' : `network or CORS refusal (${(e as Error).name})` };
    }
    if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400)) {
      const loc = res.headers.get('location');
      let location: string | null = null;
      if (loc) {
        const target = admitUrl(policy, new URL(loc, url).href);
        location = target.ok ? target.url.href : null;
      }
      void res.body?.cancel();
      return { kind: 'redirect', url, status: res.status, location };
    }
    if (!res.ok) {
      void res.body?.cancel();
      return { kind: 'failed', url, reason: `HTTP ${res.status}` };
    }
    const contentType = (res.headers.get('content-type') ?? '').split(';')[0]!.trim().toLowerCase();
    if (!HTML_TYPES.includes(contentType as (typeof HTML_TYPES)[number])) {
      void res.body?.cancel();
      return { kind: 'failed', url, reason: `not HTML (${contentType || 'no content-type'})` };
    }
    let read: { body: Uint8Array<ArrayBuffer>; truncated: boolean };
    try {
      read = await readCapped(res, policy.maxBodyBytes);
    } catch {
      return { kind: 'failed', url, reason: ac.signal.aborted ? 'timed out' : 'body read failed' };
    }
    return {
      kind: 'page',
      url,
      status: res.status,
      contentType,
      body: read.body,
      byteLen: read.body.length,
      contentHash: await sha256Hex(read.body),
      truncated: read.truncated,
    };
  } finally {
    clearTimeout(timer);
  }
}
