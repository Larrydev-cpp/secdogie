/**
 * Signed envelopes, mirroring `secdogie_identity.signing`: the payload plus
 * `signer` (did:key) and `sig` (base64 of an Ed25519 signature over the
 * canonical bytes of the payload *without* those two keys). Layers onto any
 * JSON contract that ignores unknown keys.
 */

import { type CanonObject, type CanonValue, canonicalBytes, isCanonObject } from './canon.ts';
import { isDidKey } from './did.ts';
import { type Signer, base64Decode, base64Encode, verifyEd25519 } from './ed25519.ts';

const ENVELOPE_KEYS = ['signer', 'sig'] as const;

/**
 * Who may sign. Zero trust: there is no "anyone" -- an empty set cannot be
 * built, as `secdogie_identity.require_trust` refuses a missing allowlist.
 */
export class TrustSet {
  readonly #dids: ReadonlySet<string>;

  constructor(dids: Iterable<string>) {
    const set = new Set<string>();
    for (const d of dids) {
      if (!isDidKey(d)) throw new Error(`not an Ed25519 did:key: ${d}`);
      set.add(d);
    }
    if (set.size === 0) throw new Error('no trusted DIDs (zero trust: an empty set trusts no one)');
    this.#dids = set;
  }

  has(did: string): boolean {
    return this.#dids.has(did);
  }

  get dids(): readonly string[] {
    return [...this.#dids];
  }
}

export async function signPayload(signer: Signer, payload: CanonObject): Promise<CanonObject> {
  for (const k of ENVELOPE_KEYS) {
    if (Object.hasOwn(payload, k)) throw new Error("payload already contains a 'signer'/'sig' envelope");
  }
  const sig = await signer.sign(canonicalBytes(payload));
  return { ...payload, signer: signer.did, sig: base64Encode(sig) };
}

export interface Verification {
  /** Signature valid and (when a trust set was given) the signer is on it. */
  readonly ok: boolean;
  /** The signer, whenever the signature itself was valid. */
  readonly signer: string | null;
}

/** The payload an envelope signs: everything but `signer` / `sig`. */
export function payloadOf(obj: CanonObject): CanonObject {
  const out: Record<string, CanonValue> = Object.create(null);
  for (const [k, v] of Object.entries(obj)) if (k !== 'signer' && k !== 'sig') out[k] = v;
  return out;
}

/**
 * Same three outcomes as Python's `verify_payload`:
 * `{ok: true, signer}` valid and trusted; `{ok: false, signer}` valid but not
 * trusted; `{ok: false, signer: null}` missing, malformed or forged.
 */
export async function verifyPayload(obj: unknown, trust?: TrustSet): Promise<Verification> {
  if (!isCanonObject(obj)) return { ok: false, signer: null };
  const signer = obj['signer'];
  const sigB64 = obj['sig'];
  if (typeof signer !== 'string' || typeof sigB64 !== 'string') return { ok: false, signer: null };
  let sig: Uint8Array<ArrayBuffer>;
  let bytes: Uint8Array<ArrayBuffer>;
  try {
    sig = base64Decode(sigB64);
    bytes = canonicalBytes(payloadOf(obj));
  } catch {
    return { ok: false, signer: null };
  }
  if (!(await verifyEd25519(signer, bytes, sig))) return { ok: false, signer: null };
  if (trust !== undefined && !trust.has(signer)) return { ok: false, signer };
  return { ok: true, signer };
}
