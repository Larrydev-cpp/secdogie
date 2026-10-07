/**
 * One page <-> node dialogue session, a port of
 * `dialogue/secdogie_dialogue/session.py`: the same frames, the same rules.
 *
 *   b"D" msg_id:u64 idx:u16 total:u16 flags:u8 chunk     a fragment (bit0 = reliable)
 *   b"A" msg_id:u64                                    an acknowledgment
 *
 *  - **Fragments.** A sealed envelope is cut into 16 KiB chunks; the receiver
 *    reassembles at most 32 messages at once, each within 4 MiB and 10 s.
 *  - **Reliability.** Everything but heartbeats is reliable: retransmitted
 *    (0.25 s, doubling) until acknowledged, given up after 6 tries
 *    (`onUndeliverable`). The receiver acknowledges after reassembly and
 *    delivers a duplicate only once (the envelope's replay guard).
 *  - **Liveness.** A heartbeat every 2 s; a peer not heard for 3 intervals is
 *    down (`onPeerDown`) and up again when heard (`onPeerUp`). A background
 *    tab's timers are throttled, so receiving also sends a heartbeat that is
 *    due -- the node keeps hearing a page that is merely in the background.
 *
 * Only envelopes signed by the session's peer are delivered; heartbeats never
 * are. All time comes in through `tick(now)`, so tests drive it with a fake
 * clock.
 */

import { canonicalBytes, parseLossless } from '../core/canon.ts';
import type { Signer } from '../core/ed25519.ts';
import type { TrustSet } from '../core/envelope.ts';
import { type Header, type Packet, PacketKind, ReplayGuard, Sender, SessionEvent, openEnvelope } from '../gate2/wire.ts';

export const FRAGMENT_SIZE = 16 * 1024;
export const MAX_MESSAGE = 4 * 1024 * 1024;
export const MAX_REASSEMBLIES = 32;
export const REASSEMBLY_TIMEOUT = 10;
export const RETRY_BASE = 0.25;
export const RETRY_MAX = 6;
export const HEARTBEAT_INTERVAL = 2;
export const DEAD_AFTER = 3;

const RELIABLE = 1;

export interface Delivered {
  readonly header: Header;
  readonly signer: string;
  readonly packet: Packet;
}

export function defaultReliable(p: Packet): boolean {
  if (p.kind === PacketKind.StateSnapshot) return false;
  if (p.kind === PacketKind.Session && p.packet.event === SessionEvent.Heartbeat) return false;
  return true;
}

export function fragments(msgId: bigint, data: Uint8Array, opts: { reliable: boolean; size?: number }): Uint8Array<ArrayBuffer>[] {
  const size = opts.size ?? FRAGMENT_SIZE;
  const total = Math.max(1, Math.ceil(data.length / size));
  if (total > 0xffff) throw new Error('message too large to fragment');
  const out: Uint8Array<ArrayBuffer>[] = [];
  for (let i = 0; i < total; i++) {
    const chunk = data.subarray(i * size, (i + 1) * size);
    const f = new Uint8Array(14 + chunk.length);
    const v = new DataView(f.buffer);
    f[0] = 0x44; // 'D'
    v.setBigUint64(1, msgId);
    v.setUint16(9, i);
    v.setUint16(11, total);
    v.setUint8(13, opts.reliable ? RELIABLE : 0);
    f.set(chunk, 14);
    out.push(f);
  }
  return out;
}

export function ackFrame(msgId: bigint): Uint8Array<ArrayBuffer> {
  const f = new Uint8Array(9);
  f[0] = 0x41; // 'A'
  new DataView(f.buffer).setBigUint64(1, msgId);
  return f;
}

interface Outstanding {
  packet: Packet;
  frames: Uint8Array<ArrayBuffer>[];
  attempts: number;
  nextAt: number;
}

interface Reassembly {
  total: number;
  reliable: boolean;
  started: number;
  parts: Map<number, Uint8Array>;
  size: number;
}

function randomMsgId(): bigint {
  const b = crypto.getRandomValues(new Uint8Array(8));
  let n = 0n;
  for (const x of b) n = (n << 8n) | BigInt(x);
  return n >> 2n; // 62 bits, as secrets.randbits(62)
}

export interface SessionOptions {
  self: Signer;
  peerDid: string;
  send: (frame: Uint8Array<ArrayBuffer>) => void;
  trust: TrustSet;
  replay?: ReplayGuard;
  /** Seconds, monotonic. */
  clock?: () => number;
  clockNs?: () => bigint;
  fragmentSize?: number;
  retryBase?: number;
  retryMax?: number;
  heartbeatInterval?: number;
  deadAfter?: number;
}

export class DialogueSession {
  readonly self: Signer;
  readonly peerDid: string;
  onEnvelope: ((d: Delivered) => void) | null = null;
  onUndeliverable: ((msgId: bigint, packet: Packet) => void) | null = null;
  onPeerDown: (() => void) | null = null;
  onPeerUp: (() => void) | null = null;

  readonly #send: (frame: Uint8Array<ArrayBuffer>) => void;
  readonly #trust: TrustSet;
  readonly #replay: ReplayGuard;
  readonly #sender: Sender;
  readonly #clock: () => number;
  readonly #fragmentSize: number;
  readonly #retryBase: number;
  readonly #retryMax: number;
  readonly #heartbeat: number;
  readonly #deadAfter: number;
  #nextMsgId = randomMsgId();
  readonly #outstanding = new Map<bigint, Outstanding>();
  readonly #reassembly = new Map<bigint, Reassembly>();
  #lastHeard: number;
  #nextHeartbeat: number;
  #alive = true;
  #chain: Promise<unknown> = Promise.resolve();
  #timer: ReturnType<typeof setInterval> | null = null;

  constructor(o: SessionOptions) {
    this.self = o.self;
    this.peerDid = o.peerDid;
    this.#send = o.send;
    this.#trust = o.trust;
    this.#clock = o.clock ?? (() => performance.now() / 1000);
    this.#replay = o.replay ?? new ReplayGuard(o.clockNs ? { clockNs: o.clockNs } : {});
    this.#sender = new Sender(o.self, o.peerDid, o.clockNs ? { clockNs: o.clockNs } : {});
    this.#fragmentSize = o.fragmentSize ?? FRAGMENT_SIZE;
    this.#retryBase = o.retryBase ?? RETRY_BASE;
    this.#retryMax = o.retryMax ?? RETRY_MAX;
    this.#heartbeat = o.heartbeatInterval ?? HEARTBEAT_INTERVAL;
    this.#deadAfter = o.deadAfter ?? DEAD_AFTER;
    const now = this.#clock();
    this.#lastHeard = now;
    this.#nextHeartbeat = now + this.#heartbeat;
  }

  get alive(): boolean {
    return this.#alive;
  }

  /** Reliable messages not yet acknowledged. */
  get pending(): number {
    return this.#outstanding.size;
  }

  // -- sending -----------------------------------------------------------------

  /** Seals and sends `p`; resolves to its message id. Sends go out in call order. */
  send(p: Packet, opts: { reliable?: boolean } = {}): Promise<bigint> {
    const reliable = opts.reliable ?? defaultReliable(p);
    const run = async (): Promise<bigint> => {
      const data = canonicalBytes(await this.#sender.seal(p));
      if (data.length > MAX_MESSAGE) throw new Error("packet exceeds the session's message size limit");
      const msgId = this.#nextMsgId;
      this.#nextMsgId += 1n;
      const frames = fragments(msgId, data, { reliable, size: this.#fragmentSize });
      if (reliable) this.#outstanding.set(msgId, { packet: p, frames, attempts: 1, nextAt: this.#clock() + this.#retryBase });
      this.#emit(frames);
      return msgId;
    };
    const result = this.#chain.then(run, run);
    this.#chain = result.catch(() => undefined);
    return result;
  }

  // -- receiving ---------------------------------------------------------------

  /** One frame from the peer (already authenticated by the frame layer). Never throws for bad input. */
  async receive(frame: Uint8Array): Promise<void> {
    if (frame.length === 0) return;
    const now = this.#clock();
    this.#heard(now);
    if (now >= this.#nextHeartbeat) {
      this.#nextHeartbeat = now + this.#heartbeat; // piggyback: a throttled tab still says it is here
      void this.send({ kind: PacketKind.Session, packet: { event: SessionEvent.Heartbeat, note: '' } });
    }
    const tag = frame[0];
    const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
    if (tag === 0x41 && frame.length === 9) {
      this.#outstanding.delete(view.getBigUint64(1));
      return;
    }
    if (tag !== 0x44 || frame.length < 14) return;
    const msgId = view.getBigUint64(1);
    const idx = view.getUint16(9);
    const total = view.getUint16(11);
    const reliable = (view.getUint8(13) & RELIABLE) === RELIABLE;
    const done = this.#reassemble(now, msgId, idx, total, reliable, frame.subarray(14));
    if (done === null) return;
    if (done.reliable) this.#emit([ackFrame(msgId)]);
    await this.#open(done.data);
  }

  #reassemble(now: number, msgId: bigint, idx: number, total: number, reliable: boolean, chunk: Uint8Array) {
    if (total === 0 || idx >= total || total * this.#fragmentSize > MAX_MESSAGE + this.#fragmentSize) return null;
    if (chunk.length > this.#fragmentSize) return null;
    let r = this.#reassembly.get(msgId);
    if (r === undefined) {
      if (this.#reassembly.size >= MAX_REASSEMBLIES) {
        let oldest: bigint | null = null;
        for (const [m, x] of this.#reassembly) if (oldest === null || x.started < this.#reassembly.get(oldest)!.started) oldest = m;
        if (oldest !== null) this.#reassembly.delete(oldest);
      }
      r = { total, reliable, started: now, parts: new Map(), size: 0 };
      this.#reassembly.set(msgId, r);
    }
    if (r.total !== total || r.reliable !== reliable || r.parts.has(idx)) return null;
    if (r.size + chunk.length > MAX_MESSAGE) {
      this.#reassembly.delete(msgId);
      return null;
    }
    r.parts.set(idx, chunk.slice());
    r.size += chunk.length;
    if (r.parts.size < r.total) return null;
    this.#reassembly.delete(msgId);
    const data = new Uint8Array(r.size);
    let off = 0;
    for (let i = 0; i < r.total; i++) {
      const part = r.parts.get(i)!;
      data.set(part, off);
      off += part.length;
    }
    return { data, reliable: r.reliable };
  }

  async #open(data: Uint8Array): Promise<void> {
    let obj: unknown;
    try {
      obj = parseLossless(new TextDecoder('utf-8', { fatal: true }).decode(data), { maxLength: MAX_MESSAGE });
    } catch {
      return;
    }
    const opened = await openEnvelope(obj, { trust: this.#trust, selfDid: this.self.did, replay: this.#replay });
    if (!opened.ok || opened.signer !== this.peerDid) return;
    if (opened.packet.kind === PacketKind.Session && opened.packet.packet.event === SessionEvent.Heartbeat) return;
    this.onEnvelope?.({ header: opened.header, signer: opened.signer, packet: opened.packet });
  }

  // -- time --------------------------------------------------------------------

  /** Retransmit, give up, expire reassemblies, heartbeat, judge liveness. */
  tick(now: number = this.#clock()): void {
    const resend: Uint8Array<ArrayBuffer>[] = [];
    const failed: Array<[bigint, Packet]> = [];
    for (const [msgId, o] of this.#outstanding) {
      if (now < o.nextAt) continue;
      if (o.attempts >= this.#retryMax) {
        this.#outstanding.delete(msgId);
        failed.push([msgId, o.packet]);
        continue;
      }
      o.attempts += 1;
      o.nextAt = now + this.#retryBase * 2 ** (o.attempts - 1);
      resend.push(...o.frames);
    }
    for (const [msgId, r] of this.#reassembly) if (now - r.started > REASSEMBLY_TIMEOUT) this.#reassembly.delete(msgId);
    const heartbeat = now >= this.#nextHeartbeat;
    if (heartbeat) this.#nextHeartbeat = now + this.#heartbeat;
    const wentDown = this.#alive && now - this.#lastHeard > this.#deadAfter * this.#heartbeat;
    if (wentDown) this.#alive = false;
    this.#emit(resend);
    if (heartbeat) void this.send({ kind: PacketKind.Session, packet: { event: SessionEvent.Heartbeat, note: '' } });
    for (const [msgId, packet] of failed) this.onUndeliverable?.(msgId, packet);
    if (wentDown) this.onPeerDown?.();
  }

  #heard(now: number): void {
    this.#lastHeard = now;
    if (!this.#alive) {
      this.#alive = true;
      this.onPeerUp?.();
    }
  }

  start(intervalMs = 100): void {
    if (this.#timer !== null) return;
    this.#timer = setInterval(() => {
      try {
        this.tick();
      } catch {
        // keep ticking; a callback bug must not end the session
      }
    }, intervalMs);
  }

  stop(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#timer = null;
  }

  #emit(frames: Uint8Array<ArrayBuffer>[]): void {
    for (const f of frames) {
      try {
        this.#send(f);
      } catch {
        // a failed send is a lost datagram: retransmission covers it
      }
    }
  }
}
