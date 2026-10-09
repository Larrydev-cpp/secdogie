/**
 * libsodium's `crypto_kx`, client side, over WebCrypto X25519 plus BLAKE2b:
 *
 *   q        = X25519(client_sk, server_pk)
 *   rx || tx = BLAKE2b-512(q || client_pk || server_pk)
 *
 * byte for byte what the node computes with PyNaCl as the server
 * (fixtures/vectors/symbiont_link.json). The X25519 private key is generated
 * non-extractable and lives only in this page's memory; the shared secret and
 * the 64 hashed bytes are imported as two non-extractable AES-GCM keys and
 * zeroed at once. What JavaScript cannot wipe -- the browser's own copies
 * inside CryptoKey objects -- is released when the keys are dropped.
 */

import { blake2b512 } from './blake2b.ts';

export const KX_PUBLIC_BYTES = 32;

export class KxError extends Error {}

export interface KxKeyPair {
  readonly publicKey: Uint8Array<ArrayBuffer>;
  readonly privateKey: CryptoKey;
}

/** The two directional AES-256-GCM keys of one link. */
export interface SessionKeys {
  /** Opens what the node sends. */
  readonly rx: CryptoKey;
  /** Seals what the page sends. */
  readonly tx: CryptoKey;
}

/** Whether this browser has WebCrypto X25519 (no fallback is ever used). */
export async function kxSupported(): Promise<boolean> {
  try {
    await crypto.subtle.generateKey({ name: 'X25519' }, false, ['deriveBits']);
    return true;
  } catch {
    return false;
  }
}

/** A fresh key pair for one handshake; the private half is never bytes in JS. */
export async function generateKxKeyPair(): Promise<KxKeyPair> {
  const pair = (await crypto.subtle.generateKey({ name: 'X25519' }, false, ['deriveBits'])) as CryptoKeyPair;
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  return { publicKey, privateKey: pair.privateKey };
}

/** A key pair from known bytes -- for the test vectors only. */
export async function kxKeyPairFromSecretForTests(secret: Uint8Array, publicKey: Uint8Array): Promise<KxKeyPair> {
  const b64 = (u: Uint8Array) => btoa(String.fromCharCode(...u)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const privateKey = await crypto.subtle.importKey(
    'jwk',
    { kty: 'OKP', crv: 'X25519', d: b64(secret), x: b64(publicKey) },
    { name: 'X25519' },
    false,
    ['deriveBits'],
  );
  return { publicKey: Uint8Array.from(publicKey), privateKey };
}

/**
 * `crypto_kx_client_session_keys` as raw bytes: `[rx, tx]`. The caller owns
 * both buffers and must zero them; {@link clientSessionKeys} does.
 */
export async function clientSessionKeyBytes(
  own: KxKeyPair,
  serverPublic: Uint8Array,
): Promise<[Uint8Array<ArrayBuffer>, Uint8Array<ArrayBuffer>]> {
  if (serverPublic.length !== KX_PUBLIC_BYTES) throw new KxError('a crypto_kx public key is 32 bytes');
  let q: Uint8Array<ArrayBuffer>;
  try {
    const peer = await crypto.subtle.importKey('raw', Uint8Array.from(serverPublic), { name: 'X25519' }, true, []);
    q = new Uint8Array(await crypto.subtle.deriveBits({ name: 'X25519', public: peer }, own.privateKey, 256));
  } catch {
    throw new KxError("the node's key was refused");
  }
  try {
    if (q.every((b) => b === 0)) throw new KxError("the node's key was refused");
    const h = blake2b512(q, own.publicKey, serverPublic);
    const rx = h.slice(0, 32);
    const tx = h.slice(32, 64);
    h.fill(0);
    return [rx, tx];
  } finally {
    q.fill(0);
  }
}

/** The page's session keys for one link, as non-extractable AES-GCM keys. */
export async function clientSessionKeys(own: KxKeyPair, serverPublic: Uint8Array): Promise<SessionKeys> {
  const [rxRaw, txRaw] = await clientSessionKeyBytes(own, serverPublic);
  try {
    const rx = await crypto.subtle.importKey('raw', rxRaw, { name: 'AES-GCM' }, false, ['decrypt']);
    const tx = await crypto.subtle.importKey('raw', txRaw, { name: 'AES-GCM' }, false, ['encrypt']);
    return { rx, tx };
  } finally {
    rxRaw.fill(0);
    txRaw.fill(0);
  }
}
