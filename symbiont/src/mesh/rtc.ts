/**
 * The browser end of the mesh's WebRTC carrier (Python: `rtc.RtcWire`).
 *
 * A browser node only dials: it joins the lobby room of each entry node its
 * operator named (the signaling gateway pairs two peers per room), makes the
 * offer with every ICE candidate already in the SDP (aiortc answers without
 * trickle), and opens one ordered DataChannel. Before a single frame is
 * delivered, both sides exchange a signed `rtc-hello/v1` binding their DIDs to
 * the DTLS fingerprints of this very connection, so the signaling gateway
 * cannot sit in the middle. The browser presents its operator admission in the
 * hello; it accepts only peers on its bundle's mesh list.
 *
 * A link that drops is redialed with backoff (2 s doubling to 30 s) while the
 * node runs; `close()` ends everything.
 */

import { type CanonObject, canonicalText, parseLossless } from '../core/canon.ts';
import { type BrowserNode, type MeshLink, wallClock } from './node.ts';
import { type Signer } from '../core/ed25519.ts';
import { checkHello, lobbyRoom, makeHello, sdpFingerprint } from './protocol.ts';

export const CHANNEL_LABEL = 'secdogie-inference';
const HANDSHAKE_MS = 20_000;
const GATHER_MS = 5_000;
const REDIAL_FIRST_MS = 2_000;
const REDIAL_MAX_MS = 30_000;
const BUFFER_HIGH = 1 << 20;
const INBOX_MAX = 256;

export interface RtcOptions {
  readonly signalingUrl: string;
  readonly salt?: string;
  readonly iceServers?: RTCIceServer[];
  /** This browser's operator admission, presented in every hello. */
  readonly admission?: CanonObject;
  readonly clock?: () => number;
  readonly log?: (line: string) => void;
}

interface Link {
  readonly pc: RTCPeerConnection;
  readonly channel: RTCDataChannel;
}

/** Messages of a socket or channel, awaited one at a time. */
class Inbox<T> {
  readonly #items: T[] = [];
  #waiter: ((v: T | Error) => void) | null = null;
  #error: Error | null = null;

  push(item: T): void {
    if (this.#waiter !== null) {
      const w = this.#waiter;
      this.#waiter = null;
      w(item);
    } else if (this.#items.length < INBOX_MAX) {
      this.#items.push(item);
    }
  }

  fail(e: Error): void {
    this.#error = e;
    if (this.#waiter !== null) {
      const w = this.#waiter;
      this.#waiter = null;
      w(e);
    }
  }

  next(): Promise<T> {
    if (this.#items.length) return Promise.resolve(this.#items.shift()!);
    if (this.#error !== null) return Promise.reject(this.#error);
    return new Promise((resolve, reject) => {
      this.#waiter = (v) => (v instanceof Error ? reject(v) : resolve(v));
    });
  }

  drain(): T[] {
    return this.#items.splice(0);
  }
}

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([
    p,
    new Promise<T>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${what}: timed out`)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

const asText = (data: unknown): string =>
  typeof data === 'string' ? data : new TextDecoder().decode(data as ArrayBuffer);

export class BrowserRtc implements MeshLink {
  readonly #signer: Signer;
  readonly #node: BrowserNode;
  readonly #opts: RtcOptions;
  readonly #clock: () => number;
  readonly #log: (line: string) => void;
  readonly #links = new Map<string, Link>();
  readonly #wanted = new Set<string>();
  readonly #dialing = new Set<string>();
  readonly #timers = new Map<string, ReturnType<typeof setTimeout>>();
  readonly #delay = new Map<string, number>();
  readonly #live = new Set<{ close(): void }>();
  #closed = false;

  constructor(signer: Signer, node: BrowserNode, opts: RtcOptions) {
    if (signer.did !== node.did) throw new Error('the signer is not this node');
    this.#signer = signer;
    this.#node = node;
    this.#opts = opts;
    this.#clock = opts.clock ?? wallClock;
    this.#log = opts.log ?? (() => {});
    node.attach(this);
  }

  /** Keep a link to `did` (an entry node): dial now, redial whenever it drops. */
  connect(did: string): void {
    if (this.#closed || did === this.#node.did) return;
    this.#wanted.add(did);
    void this.#dial(did);
  }

  send(did: string, text: string): boolean {
    const link = this.#links.get(did);
    if (link === undefined || link.channel.readyState !== 'open' || link.channel.bufferedAmount > BUFFER_HIGH) return false;
    try {
      link.channel.send(text);
      return true;
    } catch {
      return false;
    }
  }

  peers(): string[] {
    return [...this.#links].filter(([, l]) => l.channel.readyState === 'open').map(([d]) => d).sort();
  }

  close(): void {
    this.#closed = true;
    for (const t of this.#timers.values()) clearTimeout(t);
    this.#timers.clear();
    for (const l of this.#links.values()) l.pc.close();
    this.#links.clear();
    for (const x of this.#live) x.close();
    this.#live.clear();
    this.#node.stop();
  }

  // -- dialing --------------------------------------------------------------------------------

  async #dial(did: string): Promise<void> {
    if (this.#closed || this.#dialing.has(did) || this.#links.has(did)) return;
    this.#dialing.add(did);
    try {
      await withTimeout(this.#attempt(did), HANDSHAKE_MS, 'dial');
      this.#delay.delete(did);
    } catch (e) {
      this.#log(`dial ${did.slice(-8)} failed: ${(e as Error).message}`);
      this.#redialLater(did);
    } finally {
      this.#dialing.delete(did);
    }
  }

  #redialLater(did: string): void {
    if (this.#closed || !this.#wanted.has(did) || this.#timers.has(did)) return;
    const delay = this.#delay.get(did) ?? REDIAL_FIRST_MS;
    this.#delay.set(did, Math.min(delay * 2, REDIAL_MAX_MS));
    this.#timers.set(did, setTimeout(() => {
      this.#timers.delete(did);
      void this.#dial(did);
    }, delay));
  }

  #signal(room: string): Promise<{ ws: WebSocket; inbox: Inbox<Record<string, unknown>> }> {
    const url = new URL(this.#opts.signalingUrl);
    url.searchParams.set('room', room);
    const ws = new WebSocket(url.toString());
    const inbox = new Inbox<Record<string, unknown>>();
    ws.onmessage = (ev) => {
      try {
        inbox.push(JSON.parse(asText(ev.data)) as Record<string, unknown>);
      } catch {
        /* not JSON: ignore */
      }
    };
    ws.onclose = () => inbox.fail(new Error('signaling closed'));
    this.#live.add(ws);
    return new Promise((resolve, reject) => {
      ws.onopen = () => resolve({ ws, inbox });
      ws.onerror = () => reject(new Error('signaling unreachable'));
    });
  }

  async #attempt(did: string): Promise<void> {
    const room = await lobbyRoom(did, this.#opts.salt ?? '');
    const { ws, inbox } = await this.#signal(room);
    let pc: RTCPeerConnection | null = null;
    try {
      const peerId = await this.#awaitPeer(inbox);
      pc = new RTCPeerConnection({ iceServers: this.#opts.iceServers ?? [] });
      this.#live.add(pc);
      const channel = pc.createDataChannel(CHANNEL_LABEL, { ordered: true });
      const messages = new Inbox<string>();
      channel.onmessage = (ev) => messages.push(asText(ev.data));
      channel.onclose = () => messages.fail(new Error('channel closed'));
      await pc.setLocalDescription(await pc.createOffer());
      await this.#gathered(pc);
      const offerSdp = pc.localDescription!.sdp;
      ws.send(JSON.stringify({ type: 'offer', to: peerId, payload: { type: 'offer', sdp: offerSdp } }));
      const answerSdp = await this.#awaitAnswer(inbox);
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
      await this.#opened(channel);
      ws.close();
      // The fingerprints from the SDP text each side actually sent, as the Python end reads them.
      const myFp = sdpFingerprint(offerSdp);
      const peerFp = sdpFingerprint(answerSdp);
      const hello = await makeHello(this.#signer, {
        room, localFp: myFp, remoteFp: peerFp, issuedAt: this.#clock(), admission: this.#opts.admission,
      });
      channel.send(canonicalText(hello));
      const peer = await checkHello(parseLossless(await messages.next()), this.#node.trust, {
        room, myFp, peerFp, now: this.#clock(),
      });
      if (peer !== did) throw new Error('the lobby was answered by another DID');
      this.#bind(did, pc, channel, messages);
      pc = null;
    } finally {
      ws.close();
      this.#live.delete(ws);
      if (pc !== null) {
        pc.close();
        this.#live.delete(pc);
      }
    }
  }

  async #awaitPeer(inbox: Inbox<Record<string, unknown>>): Promise<string> {
    for (;;) {
      const msg = await inbox.next();
      if (msg['type'] === 'welcome' && Array.isArray(msg['peers']) && msg['peers'].length) return String(msg['peers'][0]);
      if (msg['type'] === 'peer-joined') return String(msg['peerId']);
      if (msg['type'] === 'error') throw new Error(`signaling: ${String(msg['code'])}`);
    }
  }

  async #awaitAnswer(inbox: Inbox<Record<string, unknown>>): Promise<string> {
    for (;;) {
      const msg = await inbox.next();
      const payload = msg['payload'] as Record<string, unknown> | undefined;
      if (msg['type'] === 'answer' && typeof payload?.['sdp'] === 'string') return payload['sdp'];
      if (msg['type'] === 'peer-left' || msg['type'] === 'error') throw new Error(`signaling: ${String(msg['type'])}`);
    }
  }

  #gathered(pc: RTCPeerConnection): Promise<void> {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve) => {
      const done = (): void => {
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(done, GATHER_MS); // send what we have
      pc.addEventListener('icegatheringstatechange', () => {
        if (pc.iceGatheringState === 'complete') done();
      });
    });
  }

  #opened(channel: RTCDataChannel): Promise<void> {
    if (channel.readyState === 'open') return Promise.resolve();
    return new Promise((resolve, reject) => {
      channel.addEventListener('open', () => resolve(), { once: true });
      channel.addEventListener('close', () => reject(new Error('channel closed')), { once: true });
    });
  }

  #bind(did: string, pc: RTCPeerConnection, channel: RTCDataChannel, early: Inbox<string>): void {
    this.#live.delete(pc);
    if (this.#closed || !this.#node.bind(did)) {
      pc.close();
      return;
    }
    this.#links.get(did)?.pc.close();
    this.#links.set(did, { pc, channel });
    for (const text of early.drain()) void this.#node.handle(did, text); // frames that came with the hello
    channel.onmessage = (ev) => void this.#node.handle(did, asText(ev.data));
    const lost = (): void => {
      if (this.#links.get(did)?.channel !== channel) return;
      this.#links.delete(did);
      pc.close();
      this.#node.unbind(did);
      this.#log(`link to ${did.slice(-8)} lost`);
      this.#redialLater(did);
    };
    channel.onclose = lost;
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'failed' || pc.connectionState === 'closed') lost();
    };
    this.#log(`link to ${did.slice(-8)} bound`);
  }
}
