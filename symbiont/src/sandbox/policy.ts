/**
 * The fetch boundary of the public-source topology sandbox, fixed when the
 * sandbox starts and never widened afterwards:
 *
 *  - HTTPS only, to an explicit allowlist of exact origins -- empty means
 *    nothing, never "anywhere";
 *  - no credentials of any kind: no cookies, no HTTP auth, no userinfo in a
 *    URL, no Authorization header, no referrer;
 *  - a bounded body and a bounded time;
 *  - redirects are never followed (`redirect: 'manual'`): a redirect is
 *    reported, and its target -- if readable and itself allowed -- becomes a
 *    candidate to fetch explicitly, not a hop taken on someone else's say-so.
 */

export const DEFAULT_MAX_BODY_BYTES = 2 << 20;
export const HARD_MAX_BODY_BYTES = 8 << 20;
export const DEFAULT_TIMEOUT_MS = 10_000;
export const HARD_MAX_TIMEOUT_MS = 30_000;
export const HTML_TYPES = ['text/html', 'application/xhtml+xml'] as const;

export interface FetchPolicy {
  readonly allowedOrigins: readonly string[];
  readonly maxBodyBytes: number;
  readonly timeoutMs: number;
}

export class PolicyError extends Error {}

/** `https://host[:port]` exactly as `URL.origin` prints it. */
export function canonicalOrigin(o: string): string {
  let u: URL;
  try {
    u = new URL(o);
  } catch {
    throw new PolicyError(`not an origin: ${o}`);
  }
  if (u.protocol !== 'https:') throw new PolicyError(`only https origins are allowed: ${o}`);
  if (u.username || u.password) throw new PolicyError(`an origin carries no credentials: ${o}`);
  if (u.origin !== o) throw new PolicyError(`write the origin exactly as ${u.origin} (no path, lower-case host)`);
  return u.origin;
}

export function makePolicy(p: {
  allowedOrigins: readonly string[];
  maxBodyBytes?: number;
  timeoutMs?: number;
}): FetchPolicy {
  const allowedOrigins = [...new Set(p.allowedOrigins.map(canonicalOrigin))];
  if (allowedOrigins.length === 0) {
    throw new PolicyError('no allowed origins: the sandbox fetches nothing without an explicit allowlist');
  }
  const maxBodyBytes = p.maxBodyBytes ?? DEFAULT_MAX_BODY_BYTES;
  const timeoutMs = p.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  if (!(maxBodyBytes > 0 && maxBodyBytes <= HARD_MAX_BODY_BYTES)) {
    throw new PolicyError(`maxBodyBytes must be in 1..${HARD_MAX_BODY_BYTES}`);
  }
  if (!(timeoutMs > 0 && timeoutMs <= HARD_MAX_TIMEOUT_MS)) {
    throw new PolicyError(`timeoutMs must be in 1..${HARD_MAX_TIMEOUT_MS}`);
  }
  return Object.freeze({ allowedOrigins: Object.freeze(allowedOrigins), maxBodyBytes, timeoutMs });
}

/** The URL the sandbox may fetch, without its fragment -- or why not. */
export function admitUrl(policy: FetchPolicy, raw: string): { ok: true; url: URL } | { ok: false; reason: string } {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return { ok: false, reason: 'not a URL' };
  }
  if (u.protocol !== 'https:') return { ok: false, reason: 'not https' };
  if (u.username || u.password) return { ok: false, reason: 'credentials in the URL' };
  if (!policy.allowedOrigins.includes(u.origin)) return { ok: false, reason: 'origin not on the allowlist' };
  u.hash = '';
  return { ok: true, url: u };
}
