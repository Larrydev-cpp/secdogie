/**
 * The record layer of the browser link (`secdogie/symbiont-link/v2`) -- the
 * twin of `transport/secdogie_transport/linkcipher.py`, byte for byte:
 *
 *   'S' | seq (u64, big-endian) | AES-256-GCM(plaintext), 128-bit tag
 *   nonce = 4 zero bytes || seq
 *   AAD   = "secdogie/symbiont-link/v2" || link (32 bytes) || 'S' || seq
 *
 * Each direction's seq starts at 0 and goes up by exactly one. A gap, a
 * replay, a forgery or a record of the wrong size closes the layer for good;
 * so does a record that could not be sent, since the node would see the gap.
 * Sealing and opening each run one at a time, in call order, so concurrent
 * callers can never put seqs on the wire out of order.
 */

import { fromHex } from '../core/canon.ts';
import type { SessionKeys } from './kx.ts';

export const PROTOCOL = 'secdogie/symbiont-link/v2';
export const HEADER_BYTES = 9;
export const TAG_BYTES = 16;
export const MAX_RECORD = 65536;
export const MAX_PLAINTEXT = MAX_RECORD - HEADER_BYTES - TAG_BYTES; // 65511
export const MAX_RECORDS = 2 ** 32;

export class RecordError extends Error {}

function header(seq: number): Uint8Array<ArrayBuffer> {
  const h = new Uint8Array(HEADER_BYTES);
  h[0] = 0x53; // 'S'
  new DataView(h.buffer).setBigUint64(1, BigInt(seq), false);
  return h;
}

function nonce(seq: number): Uint8Array<ArrayBuffer> {
  const n = new Uint8Array(12);
  new DataView(n.buffer).setBigUint64(4, BigInt(seq), false);
  return n;
}

export class RecordLayer {
  #rx: CryptoKey | null;
  #tx: CryptoKey | null;
  readonly #prefix: Uint8Array<ArrayBuffer>;
  #sendSeq = 0;
  #recvSeq = 0;
  #sending: Promise<unknown> = Promise.resolve();
  #opening: Promise<unknown> = Promise.resolve();
  #closed: string | null = null;

  /** `link` is the 64-hex link digest both ends computed from W1. */
  constructor(keys: SessionKeys, link: string) {
    const linkBytes = fromHex(link);
    if (linkBytes.length !== 32) throw new RecordError('a link digest is 32 bytes');
    this.#rx = keys.rx;
    this.#tx = keys.tx;
    const proto = new TextEncoder().encode(PROTOCOL);
    this.#prefix = new Uint8Array(proto.length + 32);
    this.#prefix.set(proto);
    this.#prefix.set(linkBytes, proto.length);
  }

  get closed(): string | null {
    return this.#closed;
  }

  /** Drops both keys; every later seal or open fails. */
  zeroize(reason = 'closed'): void {
    if (this.#closed === null) this.#closed = reason;
    this.#rx = null;
    this.#tx = null;
    this.#sendSeq = 0;
    this.#recvSeq = 0;
  }

  #aad(h: Uint8Array): Uint8Array<ArrayBuffer> {
    const aad = new Uint8Array(this.#prefix.length + h.length);
    aad.set(this.#prefix);
    aad.set(h, this.#prefix.length);
    return aad;
  }

  /**
   * Seal `plaintext` and hand the record to `send`, after every earlier call
   * has finished. The seq is spent only when `send` resolves; if sealing or
   * sending fails the layer closes and the error propagates.
   */
  sealAndSend(plaintext: Uint8Array, send: (record: Uint8Array<ArrayBuffer>) => Promise<void> | void): Promise<void> {
    const run = async (): Promise<void> => {
      if (this.#closed !== null) throw new RecordError(`the record layer is closed (${this.#closed})`);
      if (plaintext.length > MAX_PLAINTEXT) throw new RecordError('a record holds at most 65511 bytes');
      if (this.#sendSeq >= MAX_RECORDS) {
        this.zeroize('too many records');
        throw new RecordError('too many records on one link');
      }
      const key = this.#tx!;
      const seq = this.#sendSeq;
      const h = header(seq);
      let record: Uint8Array<ArrayBuffer>;
      try {
        const ct = new Uint8Array(
          await crypto.subtle.encrypt(
            { name: 'AES-GCM', iv: nonce(seq), additionalData: this.#aad(h), tagLength: 128 },
            key,
            Uint8Array.from(plaintext),
          ),
        );
        record = new Uint8Array(HEADER_BYTES + ct.length);
        record.set(h);
        record.set(ct, HEADER_BYTES);
        await send(record);
      } catch (e) {
        this.zeroize('a record could not be sent');
        throw e instanceof RecordError ? e : new RecordError('a record could not be sent');
      }
      if (this.#closed !== null) throw new RecordError(`the record layer is closed (${this.#closed})`);
      this.#sendSeq = seq + 1;
    };
    const p = this.#sending.then(run, run);
    this.#sending = p.catch(() => undefined);
    return p;
  }

  /** Open the next record from the node, after every earlier call has finished. */
  open(record: Uint8Array): Promise<Uint8Array<ArrayBuffer>> {
    const run = async (): Promise<Uint8Array<ArrayBuffer>> => {
      if (this.#closed !== null) throw new RecordError(`the record layer is closed (${this.#closed})`);
      const fail = (why: string): never => {
        this.zeroize(why);
        throw new RecordError(why);
      };
      if (record.length < HEADER_BYTES + TAG_BYTES || record.length > MAX_RECORD) fail('a record of the wrong size');
      if (record[0] !== 0x53) fail('not a record');
      const seq = new DataView(record.buffer, record.byteOffset, record.byteLength).getBigUint64(1, false);
      if (seq !== BigInt(this.#recvSeq)) fail('a record out of sequence');
      const n = Number(seq);
      let plain: Uint8Array<ArrayBuffer>;
      try {
        plain = new Uint8Array(
          await crypto.subtle.decrypt(
            { name: 'AES-GCM', iv: nonce(n), additionalData: this.#aad(record.subarray(0, HEADER_BYTES)), tagLength: 128 },
            this.#rx!,
            Uint8Array.from(record.subarray(HEADER_BYTES)),
          ),
        );
      } catch {
        return fail('a record failed to authenticate');
      }
      if (this.#closed !== null) throw new RecordError(`the record layer is closed (${this.#closed})`);
      this.#recvSeq = n + 1;
      return plain;
    };
    const p = this.#opening.then(run, run);
    this.#opening = p.catch(() => undefined);
    return p;
  }
}
