// The browser's side of the frames every secdogie link carries: the same
// DID-signed `secdogie/direct/v1` frame as transport/secdogie_transport/udp.py,
// with ChannelMux's channel framing inside (mux.py), and the same sliding
// replay window (sealed.py). A frame is believed because of its signature,
// never because of the pipe it came on.
//
// Counters are BigInt: Python starts them at time_ns() (about 1.8e18), past
// what a JS number holds exactly, so frames are parsed with their integers'
// source text and written with canonical JSON, which keeps every digit.

import { base64ToBytes, bytesToBase64, canonical, signPayload, verifyPayload } from './pairing.js';

export const FRAME_TYPE = 'secdogie/direct/v1';
export const REPLAY_WINDOW = 1024;
const CHANNEL = /^[A-Za-z0-9._/-]{1,64}$/;
const ascii = new TextEncoder();

/** JSON.parse that keeps integers beyond 2^53 exact, as BigInt. */
export function parseJson(text) {
  return JSON.parse(text, (key, value, context) => (
    typeof value === 'number' && !Number.isSafeInteger(value) && context?.source && /^-?\d+$/.test(context.source)
      ? BigInt(context.source)
      : value
  ));
}

/** A counter that keeps increasing across page reloads: nanoseconds since 1970, then +1 per frame. */
export function newCounter() {
  let ctr = BigInt(Date.now()) * 1_000_000n;
  return () => {
    ctr += 1n;
    return ctr;
  };
}

/** The frame text carrying `message` (bytes) from `identity` to `toDid`. */
export async function encodeFrame(identity, toDid, message, ctr) {
  const payload = { t: FRAME_TYPE, from: identity.did, to: toDid, ctr: BigInt(ctr), data: bytesToBase64(message) };
  return canonical(await signPayload(identity, payload));
}

/**
 * `{from, ctr, message}` for a frame signed by one of `allow` (DIDs) and meant
 * for `selfDid`; null for anything else.
 */
export async function decodeFrame(text, { allow, selfDid }) {
  let obj;
  try {
    obj = parseJson(text);
  } catch {
    return null;
  }
  if (!obj || typeof obj !== 'object' || obj.t !== FRAME_TYPE) return null;
  const { ok, signer } = await verifyPayload(obj);
  if (!ok || !new Set(allow).has(signer) || obj.from !== signer || obj.to !== selfDid) return null;
  const ctr = typeof obj.ctr === 'number' && Number.isSafeInteger(obj.ctr) ? BigInt(obj.ctr) : obj.ctr;
  if (typeof ctr !== 'bigint' || ctr < 0n || typeof obj.data !== 'string') return null;
  try {
    return { from: signer, ctr, message: base64ToBytes(obj.data) };
  } catch {
    return null;
  }
}

/** ChannelMux framing: one byte of name length, the ASCII name, the channel's bytes. */
export function muxEncode(channel, payload) {
  if (!CHANNEL.test(channel)) throw new TypeError(`bad channel name ${channel}`);
  const name = ascii.encode(channel);
  const out = new Uint8Array(1 + name.length + payload.length);
  out[0] = name.length;
  out.set(name, 1);
  out.set(payload, 1 + name.length);
  return out;
}

export function muxDecode(message) {
  if (!message?.length) return null;
  const n = message[0];
  if (message.length < 1 + n) return null;
  const channel = String.fromCharCode(...message.subarray(1, 1 + n));
  if (!CHANNEL.test(channel)) return null;
  return { channel, payload: message.subarray(1 + n) };
}

/** The WireGuard-style window of sealed.py: each counter once; too far behind the highest is too old. */
export class ReplayWindow {
  #highest = null;
  #seen = new Set(); // counters within the window below the highest

  constructor(size = REPLAY_WINDOW) {
    this.size = BigInt(size);
  }

  accept(ctr) {
    if (this.#highest === null || ctr > this.#highest) {
      this.#highest = ctr;
      this.#seen.add(ctr);
      for (const c of this.#seen) if (this.#highest - c >= this.size) this.#seen.delete(c);
      return true;
    }
    if (this.#highest - ctr >= this.size || this.#seen.has(ctr)) return false;
    this.#seen.add(ctr);
    return true;
  }
}
