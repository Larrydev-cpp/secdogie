/**
 * `did:key` for Ed25519, as in `secdogie_identity/did.py` and `graph/src/did.rs`:
 * the multicodec prefix 0xed 0x01 plus the 32-byte public key, base58btc behind
 * the `z` multibase tag. base58btc is inline rather than a dependency.
 */

const PREFIX = 'did:key:z';
const ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const INDEX = new Map([...ALPHABET].map((c, i) => [c, i]));
const MAX_ENCODED = 64;

export class DidError extends Error {}

function b58encode(data: Uint8Array): string {
  const digits: number[] = []; // little-endian base-58
  for (const byte of data) {
    let carry = byte;
    for (let k = 0; k < digits.length; k++) {
      carry += digits[k]! << 8;
      digits[k] = carry % 58;
      carry = Math.floor(carry / 58);
    }
    while (carry > 0) {
      digits.push(carry % 58);
      carry = Math.floor(carry / 58);
    }
  }
  let zeros = 0;
  while (zeros < data.length && data[zeros] === 0) zeros++;
  return '1'.repeat(zeros) + digits.reverse().map((d) => ALPHABET[d]).join('');
}

function b58decode(s: string): Uint8Array<ArrayBuffer> {
  const bytes: number[] = []; // little-endian base-256
  for (const c of s) {
    const v = INDEX.get(c);
    if (v === undefined) throw new DidError(`invalid base58 character ${JSON.stringify(c)}`);
    let carry = v;
    for (let k = 0; k < bytes.length; k++) {
      carry += bytes[k]! * 58;
      bytes[k] = carry & 0xff;
      carry >>= 8;
    }
    while (carry > 0) {
      bytes.push(carry & 0xff);
      carry >>= 8;
    }
  }
  let zeros = 0;
  while (zeros < s.length && s[zeros] === '1') zeros++;
  for (let k = 0; k < zeros; k++) bytes.push(0);
  return new Uint8Array(bytes.reverse());
}

export function didFromPublicKey(pk: Uint8Array): string {
  if (pk.length !== 32) throw new DidError(`Ed25519 public key must be 32 bytes, got ${pk.length}`);
  const raw = new Uint8Array(34);
  raw.set([0xed, 0x01]);
  raw.set(pk, 2);
  return PREFIX + b58encode(raw);
}

export function publicKeyFromDid(did: string): Uint8Array<ArrayBuffer> {
  if (typeof did !== 'string' || !did.startsWith(PREFIX)) {
    throw new DidError('not a did:key (base58btc / Ed25519)');
  }
  const enc = did.slice(PREFIX.length);
  if (enc.length === 0 || enc.length > MAX_ENCODED) throw new DidError('not a did:key (base58btc / Ed25519)');
  const raw = b58decode(enc);
  if (raw.length !== 34 || raw[0] !== 0xed || raw[1] !== 0x01) {
    throw new DidError('did:key is not a 32-byte Ed25519 key');
  }
  return raw.slice(2);
}

export function isDidKey(did: string): boolean {
  try {
    publicKeyFromDid(did);
    return true;
  } catch {
    return false;
  }
}

/** A short, human-readable form for display only (never for comparison). */
export function shortDid(did: string): string {
  return did.length > 20 ? `${did.slice(0, 14)}…${did.slice(-6)}` : did;
}
