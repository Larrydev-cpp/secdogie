/**
 * The channel prefix inside a transport frame, as `transport/secdogie_transport/mux.py`:
 * one byte of name length, the ASCII name, then the channel's bytes. The
 * operator dialogue is `dialogue/v1`; `graph/v1` is reserved for state-graph
 * anti-entropy.
 */

const NAME = /^[A-Za-z0-9._/-]{1,64}$/;

export const DIALOGUE_CHANNEL = 'dialogue/v1';
export const GRAPH_CHANNEL = 'graph/v1';

export function muxEncode(channel: string, payload: Uint8Array): Uint8Array<ArrayBuffer> {
  if (!NAME.test(channel)) throw new Error(`bad channel name ${JSON.stringify(channel)}`);
  const name = new TextEncoder().encode(channel);
  const out = new Uint8Array(1 + name.length + payload.length);
  out[0] = name.length;
  out.set(name, 1);
  out.set(payload, 1 + name.length);
  return out;
}

export function muxDecode(message: Uint8Array): { channel: string; payload: Uint8Array<ArrayBuffer> } | null {
  if (message.length === 0) return null;
  const n = message[0]!;
  if (message.length < 1 + n) return null;
  const nameBytes = message.subarray(1, 1 + n);
  if (nameBytes.some((b) => b > 0x7f)) return null;
  const channel = String.fromCharCode(...nameBytes);
  if (!NAME.test(channel)) return null;
  return { channel, payload: message.slice(1 + n) };
}
