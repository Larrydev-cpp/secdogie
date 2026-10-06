/**
 * Ed25519 over WebCrypto -- native in current browsers and Node, no
 * dependency. A {@link Signer} holds a key that cannot be exported: the
 * private half never exists as bytes in JavaScript once generated.
 */

import { didFromPublicKey, publicKeyFromDid } from './did.ts';

export interface Signer {
  readonly did: string;
  sign(data: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>>;
}

// PKCS#8 wrapping of a raw 32-byte Ed25519 seed (RFC 8410).
const PKCS8_PREFIX = Uint8Array.from([
  0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
]);

export class WebCryptoSigner implements Signer {
  readonly did: string;
  readonly #key: CryptoKey;

  private constructor(did: string, key: CryptoKey) {
    this.did = did;
    this.#key = key;
  }

  /** A fresh identity whose private key is not extractable. */
  static async generate(): Promise<WebCryptoSigner> {
    const pair = (await crypto.subtle.generateKey({ name: 'Ed25519' }, false, ['sign', 'verify'])) as CryptoKeyPair;
    const pk = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
    return new WebCryptoSigner(didFromPublicKey(pk), pair.privateKey);
  }

  /**
   * From a 32-byte seed -- for golden vectors and tests, which need the same
   * key in every language. The returned key is not extractable.
   */
  static async fromSeed(seed: Uint8Array): Promise<WebCryptoSigner> {
    if (seed.length !== 32) throw new Error('an Ed25519 seed is 32 bytes');
    const pkcs8 = new Uint8Array(48);
    pkcs8.set(PKCS8_PREFIX);
    pkcs8.set(seed, 16);
    const tmp = await crypto.subtle.importKey('pkcs8', pkcs8, { name: 'Ed25519' }, true, ['sign']);
    const jwk = await crypto.subtle.exportKey('jwk', tmp);
    pkcs8.fill(0);
    const key = await crypto.subtle.importKey('jwk', jwk, { name: 'Ed25519' }, false, ['sign']);
    const pk = base64UrlDecode(jwk.x ?? '');
    jwk.d = '';
    return new WebCryptoSigner(didFromPublicKey(pk), key);
  }

  async sign(data: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
    return new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, this.#key, data));
  }
}

/** Verifies a detached Ed25519 signature by the key a did:key names. */
export async function verifyEd25519(
  did: string,
  data: Uint8Array<ArrayBuffer>,
  sig: Uint8Array<ArrayBuffer>,
): Promise<boolean> {
  if (sig.length !== 64) return false;
  let pk: Uint8Array<ArrayBuffer>;
  try {
    pk = publicKeyFromDid(did);
  } catch {
    return false;
  }
  try {
    const key = await crypto.subtle.importKey('raw', pk, { name: 'Ed25519' }, false, ['verify']);
    return await crypto.subtle.verify({ name: 'Ed25519' }, key, sig, data);
  } catch {
    return false;
  }
}

function base64UrlDecode(s: string): Uint8Array<ArrayBuffer> {
  return base64Decode(s.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(s.length / 4) * 4, '='));
}

const B64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

/** Strict standard base64 (padding required), as Python's `b64decode(validate=True)`. */
export function base64Decode(s: string): Uint8Array<ArrayBuffer> {
  if (!B64.test(s)) throw new Error('not standard base64');
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function base64Encode(bytes: Uint8Array): string {
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}
