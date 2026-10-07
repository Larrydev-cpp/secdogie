/**
 * The transport frame the page and the node exchange, one per data-channel
 * message: `secdogie/direct/v1`, exactly as `transport/secdogie_transport/udp.py`
 * builds it -- {t, from, to, ctr, data} signed by the sender's DID, `ctr` a
 * counter that starts at the wall clock in nanoseconds and only grows, `data`
 * the mux message in standard base64. The receiver checks the signature, that
 * the signer is the one peer it expects and the frame is addressed to it, and
 * the counter against a 1024-wide sliding window (`sealed.py`'s
 * `ReplayWindow`), last -- so a replayed or forged frame changes nothing.
 *
 * On a WebRTC link the frame rides inside DTLS, bound to both DIDs by W1; it is
 * the same frame a node accepts over UDP.
 */

import { type CanonObject, asBigInt, canonicalBytes, parseLossless } from '../core/canon.ts';
import { type Signer, base64Decode, base64Encode } from '../core/ed25519.ts';
import { type TrustSet, signPayload, verifyPayload } from '../core/envelope.ts';

export const FRAME_TYPE = 'secdogie/direct/v1';
export const REPLAY_WINDOW = 1024;
const MAX_FRAME = 128 * 1024;

/** The WireGuard-style window: the highest counter seen and a bitmap below it. */
export class ReplayWindow {
  readonly size: number;
  readonly #size: bigint;
  readonly #mask: bigint;
  highest: bigint | null = null;
  #bits = 0n;

  constructor(size = REPLAY_WINDOW) {
    this.size = size;
    this.#size = BigInt(size);
    this.#mask = (1n << this.#size) - 1n;
  }

  /** Records `ctr`; false (recording nothing) for a duplicate or one too old. */
  accept(ctr: bigint): boolean {
    if (this.highest === null) {
      this.highest = ctr;
      this.#bits = 1n;
      return true;
    }
    if (ctr > this.highest) {
      const shift = ctr - this.highest;
      this.#bits = shift < this.#size ? ((this.#bits << shift) | 1n) & this.#mask : 1n;
      this.highest = ctr;
      return true;
    }
    const diff = this.highest - ctr;
    if (diff >= this.#size || ((this.#bits >> diff) & 1n) === 1n) return false;
    this.#bits |= 1n << diff;
    return true;
  }
}

function wallNs(): bigint {
  // milliseconds from the wall clock plus the sub-millisecond part of the monotonic one
  return BigInt(Date.now()) * 1_000_000n + BigInt(Math.floor((performance.now() % 1) * 1e6));
}

export class DirectFrames {
  readonly self: Signer;
  readonly peerDid: string;
  readonly #trust: TrustSet;
  readonly #window = new ReplayWindow();
  #ctr: bigint;

  constructor(opts: { self: Signer; peerDid: string; trust: TrustSet; clockNs?: () => bigint }) {
    if (!opts.trust.has(opts.peerDid)) throw new Error('the peer must be trusted');
    this.self = opts.self;
    this.peerDid = opts.peerDid;
    this.#trust = opts.trust;
    this.#ctr = (opts.clockNs ?? wallNs)();
  }

  /** The signed frame carrying `message` to the peer (UTF-8 JSON bytes). */
  async build(message: Uint8Array): Promise<Uint8Array<ArrayBuffer>> {
    this.#ctr += 1n;
    return canonicalBytes(await this.frame(message, this.#ctr));
  }

  /** The signed frame object for an explicit counter (golden vectors). */
  async frame(message: Uint8Array, ctr: bigint): Promise<CanonObject> {
    return signPayload(this.self, {
      t: FRAME_TYPE,
      from: this.self.did,
      to: this.peerDid,
      ctr,
      data: base64Encode(message),
    });
  }

  /** The message an authentic, fresh frame from the peer carries, else null. */
  async open(raw: Uint8Array): Promise<Uint8Array<ArrayBuffer> | null> {
    if (raw.length > MAX_FRAME) return null;
    let obj: unknown;
    try {
      obj = parseLossless(new TextDecoder('utf-8', { fatal: true }).decode(raw), { maxLength: MAX_FRAME });
    } catch {
      return null;
    }
    if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) return null;
    const o = obj as CanonObject;
    if (o['t'] !== FRAME_TYPE) return null;
    const { ok, signer } = await verifyPayload(o, this.#trust);
    if (!ok || signer !== this.peerDid || o['from'] !== signer || o['to'] !== this.self.did) return null;
    const ctr = typeof o['ctr'] === 'boolean' ? null : asBigInt(o['ctr']);
    if (ctr === null || ctr < 0n || typeof o['data'] !== 'string') return null;
    let data: Uint8Array<ArrayBuffer>;
    try {
      data = base64Decode(o['data']);
    } catch {
      return null;
    }
    return this.#window.accept(ctr) ? data : null;
  }
}
