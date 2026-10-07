/**
 * ATTACH_OPERATOR: how the page reaches the node, with nothing typed in.
 *
 * On load the page reads its keys and its pairing record:
 *
 *  - **paired** -> it joins the node's standing room on the gateway named by
 *    the page's own build (never by a link), and waits. The node states first
 *    (W1); the page checks that statement against the paired node's DID and its
 *    own view of the DTLS fingerprints, and only then answers with its own --
 *    before that it has revealed nothing, not even its DID. From then on the
 *    link carries transport frames (`onFrame` / `send`). A `#pair=` link is
 *    ignored: a paired browser cannot be talked into another node by a link.
 *  - **not paired, opened from a pairing link** -> it meets the node in the
 *    one-time room the link derives, checks the node against the DID in the
 *    link, says a signed hello and shows the 12-digit check code; the owner
 *    compares it on the node's terminal, and the tap here (`confirmPairing`)
 *    sends the confirmation. A node-signed receipt is the only thing that
 *    writes the pairing record; then the page attaches as above.
 *  - **neither** -> nothing: no socket, no request, no address leaves the page.
 *
 * A node-signed `not-enrolled` for this very link makes the page forget its
 * pairing; a signed `busy` (or a gateway room that stays full) means another
 * page has the node. Within one browser, the newest tab takes over and older
 * ones step aside (BroadcastChannel). Lost links come back by themselves, with
 * jittered backoff; `pagehide` sends no goodbye -- the node keeps any pending
 * question until it expires, and shows it again when the page returns.
 *
 * Machinery goes to DevTools (`trace`); the page shows only {@link LinkPhase}.
 */

import { type CanonObject, canonicalText, isCanonObject, parseLossless } from '../core/canon.ts';
import { trace } from '../core/trace.ts';
import { type Keys, type Keystore, KeystoreError, type PairingRecord } from './keystore.ts';
import * as la from './linkauth.ts';
import type { IceServer, PeerApi, PeerInfo, PeerStateName } from './peer_api.ts';

export type LinkPhase =
  | 'unpaired'
  | 'pairing'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'busy'
  | 'refused'
  | 'unreachable'
  | 'demo';

export interface LinkConfig {
  /** The page's own gateway, fixed at build time. */
  readonly signalUrl: string;
  readonly iceServers: readonly IceServer[];
}

/** What the pairing card shows. */
export interface PairingView {
  readonly stage: 'checking' | 'confirm' | 'sent' | 'refused';
  /** The 12 digits, once the node's statement verified and the hello went out. */
  readonly code: string | null;
}

export interface BroadcastLike {
  postMessage(msg: unknown): void;
  onmessage: ((ev: { data: unknown }) => void) | null;
  close(): void;
}

export interface AttachDeps {
  readonly peer: PeerApi;
  readonly keystore: Keystore;
  readonly config: LinkConfig;
  /** `location.hash` at load. */
  readonly hash: string;
  /** Strip the fragment from the address bar (it is a key). */
  readonly clearHash?: () => void;
  /** Seconds since the epoch. */
  readonly now?: () => number;
  readonly random?: () => number;
  readonly timers?: { set: (fn: () => void, ms: number) => unknown; clear: (h: unknown) => void };
  /** Same-browser tabs (newest takes over); null to skip. */
  readonly tabs?: BroadcastLike | null;
  /** Whether this call runs inside a real user activation (the pairing tap). */
  readonly activation?: () => boolean;
  /** Ask the browser to keep this origin's storage (after pairing). */
  readonly persist?: () => void;
}

export interface PhaseInfo {
  /** Machine reason, for the page's wording and DevTools: 'not-enrolled', 'busy', 'another-tab', ... */
  readonly reason: string | null;
  /** True for a busy state the node did not sign (the gateway's full room). */
  readonly unverified?: boolean;
}

const BIND_TIMEOUT_MS = 10_000;
const UNREACHABLE_AFTER_MS = 8_000;
const ROOM_FULL_GRACE_MS = 10_000;
const BACKOFF_MIN_MS = 1_000;
const BACKOFF_MAX_MS = 60_000;
const MAX_W1_FAILURES = 3;

interface Link {
  readonly id: number;
  readonly local: string[];
  readonly remote: string[];
  bound: boolean;
  bindTimer: unknown;
  hello: CanonObject | null;
}

type Mode = 'standing' | 'pairing';

export class Attachment {
  phase: LinkPhase = 'connecting';
  info: PhaseInfo = { reason: null };
  pairing: PairingView | null = null;
  keys: Keys | null = null;
  record: PairingRecord | null = null;

  readonly #d: AttachDeps;
  readonly #now: () => number;
  readonly #random: () => number;
  readonly #timers: NonNullable<AttachDeps['timers']>;
  readonly #phaseListeners = new Set<(phase: LinkPhase, info: PhaseInfo) => void>();
  readonly #frameListeners = new Set<(data: Uint8Array<ArrayBuffer>) => void>();
  readonly #boundListeners = new Set<(linkId: number) => void>();
  readonly #tabId = Math.random().toString(36).slice(2);
  #mode: Mode | null = null;
  #invite: la.PairingInvite | null = null;
  #link: Link | null = null;
  #running = false;
  #retryTimer: unknown = null;
  #unreachableTimer: unknown = null;
  #retries = 0;
  #w1Failures = 0;
  #roomFullSince: number | null = null;
  #stepAside = false;
  #unsubscribe: Array<() => void> = [];

  constructor(deps: AttachDeps) {
    this.#d = deps;
    this.#now = deps.now ?? (() => Date.now() / 1000);
    this.#random = deps.random ?? Math.random;
    this.#timers = deps.timers ?? { set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>) };
  }

  onPhase(cb: (phase: LinkPhase, info: PhaseInfo) => void): () => void {
    this.#phaseListeners.add(cb);
    return () => this.#phaseListeners.delete(cb);
  }

  /** Transport frames from the node, once the link is bound. */
  onFrame(cb: (data: Uint8Array<ArrayBuffer>) => void): () => void {
    this.#frameListeners.add(cb);
    return () => this.#frameListeners.delete(cb);
  }

  /** Each time a (new) link to the node is bound -- the moment to say HELLO. */
  onBound(cb: (linkId: number) => void): () => void {
    this.#boundListeners.add(cb);
    return () => this.#boundListeners.delete(cb);
  }

  /** The node this page is paired with (or pairing with). */
  get nodeDid(): string | null {
    return this.record?.node ?? this.#invite?.nodeDid ?? null;
  }

  /** Whether the node enrolled this browser's operator key (Gate 2 from here). */
  get canApprove(): boolean {
    return !!this.record && !!this.keys && this.record.operator === this.keys.operator.did;
  }

  get bound(): boolean {
    return this.#link?.bound === true;
  }

  // -- start -------------------------------------------------------------------------

  async start(): Promise<void> {
    trace('ATTACH_OPERATOR start');
    try {
      this.keys = await this.#d.keystore.keys();
    } catch (e) {
      trace('ATTACH_OPERATOR keys unavailable', { error: (e as Error).message, keystore: e instanceof KeystoreError });
      this.#setPhase('unreachable', { reason: 'keys-unreadable' });
      return;
    }
    try {
      this.record = await this.#d.keystore.pairing();
    } catch (e) {
      trace('ATTACH_OPERATOR pairing record unreadable', { error: (e as Error).message });
      this.record = null;
    }
    const isPairLink = this.#d.hash.startsWith('#pair=');
    if (isPairLink) this.#d.clearHash?.();
    if (this.record) {
      if (isPairLink) trace('ATTACH_OPERATOR a pairing link was ignored: this browser is already paired');
      this.#joinTabs();
      this.#begin('standing');
      return;
    }
    if (isPairLink) {
      try {
        this.#invite = await la.parsePairingFragment(this.#d.hash, this.#now());
      } catch (e) {
        trace('ATTACH_OPERATOR pairing link refused', { error: (e as Error).message });
        this.#setPhase('unpaired', { reason: 'bad-link' });
        return;
      }
      this.pairing = { stage: 'checking', code: null };
      this.#setPhase('pairing', { reason: null });
      this.#begin('pairing');
      return;
    }
    this.#setPhase('unpaired', { reason: null }); // and nothing leaves the page
  }

  /** The tap on the pairing card. Must run inside the click handler. */
  async confirmPairing(): Promise<boolean> {
    const link = this.#link;
    const invite = this.#invite;
    if (this.#mode !== 'pairing' || !link?.hello || !invite || !this.keys || this.pairing?.stage !== 'confirm') return false;
    if (this.#d.activation && !this.#d.activation()) {
      trace('ATTACH_OPERATOR pairing tap refused: not a user activation');
      return false;
    }
    const now = this.#now();
    const proof = await la.createOperatorProof(this.keys.operator, {
      appDid: this.keys.app.did, nodeDid: invite.nodeDid, pairingId: invite.pairingId, issuedAt: now,
    });
    const confirm = await la.createPairConfirm(this.keys.app, { invite, hello: link.hello, operatorProof: proof, issuedAt: now });
    await this.#text(confirm);
    this.pairing = { stage: 'sent', code: this.pairing.code };
    this.#setPhase('pairing', { reason: null });
    trace('ATTACH_OPERATOR pairing confirmed by tap', { pairing: invite.pairingId.slice(0, 8) });
    return true;
  }

  cancelPairing(): void {
    if (this.#mode !== 'pairing') return;
    this.#stop();
    this.#invite = null;
    this.pairing = null;
    this.#setPhase('unpaired', { reason: null });
  }

  /** Send one transport frame; false when no link is bound. */
  async send(data: Uint8Array): Promise<boolean> {
    if (!this.#link?.bound) return false;
    try {
      await this.#d.peer.sendData(data);
      return true;
    } catch {
      return false;
    }
  }

  stop(): void {
    this.#stop();
    for (const off of this.#unsubscribe) off();
    this.#unsubscribe = [];
    this.#d.tabs?.close();
  }

  // -- the peer --------------------------------------------------------------------------

  #begin(mode: Mode): void {
    this.#mode = mode;
    if (this.#unsubscribe.length === 0) {
      this.#unsubscribe.push(this.#d.peer.onStateChange((s, detail, info) => this.#onState(s, detail, info)));
      this.#unsubscribe.push(this.#d.peer.onMessage((data) => void this.#onMessage(data)));
    }
    this.#connect();
  }

  #connect(): void {
    if (this.#stepAside) return;
    const room = this.#mode === 'standing' ? this.record?.room : this.#invite?.room;
    if (!room) return;
    this.#running = true;
    if (this.phase !== 'pairing' && this.phase !== 'busy') this.#setPhase(this.#retries ? 'reconnecting' : 'connecting', { reason: null });
    this.#armUnreachable();
    trace('ATTACH_OPERATOR joining', { mode: this.#mode, attempt: this.#retries });
    this.#d.peer
      .startPeerConnection({ signalingUrl: this.#d.config.signalUrl, room, iceServers: this.#d.config.iceServers, log: (line) => trace(`webrtc: ${line}`) })
      .catch((e: Error & { code?: string }) => {
        trace('ATTACH_OPERATOR gateway unavailable', { code: e.code ?? null });
      });
  }

  #onState(state: PeerStateName, detail: string, info: PeerInfo): void {
    trace(`ATTACH_OPERATOR peer ${state}`, { detail, code: info.code, linkId: info.linkId });
    if (state === 'connected') {
      this.#roomFullSince = null;
      if (this.#link && this.#link.id === info.linkId) {
        if (this.#link.bound && this.#mode === 'standing') this.#setPhase('connected', { reason: null }); // ICE recovered
        return;
      }
      this.#newLink(info.linkId);
      return;
    }
    if (state === 'disconnected') {
      if (this.#link?.bound && this.#mode === 'standing') this.#setPhase('reconnecting', { reason: null });
      return;
    }
    if (state === 'waiting' || state === 'negotiating') {
      if (state === 'waiting') this.#dropLink();
      return;
    }
    if (state === 'failed') {
      this.#dropLink();
      this.#running = false;
      const now = this.#now() * 1000;
      if (info.code === 'room-full') {
        this.#roomFullSince ??= now;
        if (now - this.#roomFullSince >= ROOM_FULL_GRACE_MS && this.#mode === 'standing') {
          this.#setPhase('busy', { reason: 'room-full', unverified: true });
        }
      } else {
        this.#roomFullSince = null;
      }
      this.#retry();
    }
  }

  #newLink(linkId: number | null): void {
    this.#dropLink();
    const d = this.#d.peer.descriptions();
    if (linkId === null || d.id !== linkId || !d.local || !d.remote) return;
    let local: string[];
    let remote: string[];
    try {
      if (!la.sdpIsDataOnly(d.remote)) throw new Error('the node offered something other than a data channel');
      local = la.fingerprintsFromSdp(d.local);
      remote = la.fingerprintsFromSdp(d.remote);
    } catch (e) {
      trace('ATTACH_OPERATOR link refused before W1', { error: (e as Error).message, linkId });
      this.#closeAndRetry();
      return;
    }
    const link: Link = { id: linkId, local, remote, bound: false, bindTimer: null, hello: null };
    link.bindTimer = this.#timers.set(() => {
      if (this.#link === link && !link.bound) {
        trace('ATTACH_OPERATOR the node did not state who it is in time', { linkId });
        this.#closeAndRetry();
      }
    }, BIND_TIMEOUT_MS);
    this.#link = link;
    trace('ATTACH_OPERATOR link open, waiting for the node to state who it is', { linkId, local, remote });
  }

  async #onMessage(data: string | ArrayBuffer): Promise<void> {
    const link = this.#link;
    if (!link) return;
    if (typeof data !== 'string') {
      if (link.bound) {
        const bytes = new Uint8Array(data);
        for (const cb of this.#frameListeners) cb(bytes);
      }
      return;
    }
    if (data.length > 8 * 1024) return;
    let msg: unknown;
    try {
      msg = parseLossless(data, { maxLength: 8 * 1024 });
    } catch {
      return;
    }
    if (!isCanonObject(msg)) return;
    const type = msg['type'];
    if (type === la.REFUSED_TYPE) return this.#onRefused(link, msg);
    if (type === la.PAIRED_TYPE) return this.#onPaired(link, msg);
    if (type === la.LINK_BINDING_TYPE && !link.bound && !link.hello) return this.#onNodeStatement(link, msg);
  }

  async #onNodeStatement(link: Link, msg: CanonObject): Promise<void> {
    const node = this.nodeDid;
    const room = this.#mode === 'standing' ? this.record?.room : this.#invite?.room;
    if (!node || !room || !this.keys) return;
    const r = await la.verifyLinkBinding(msg, { trust: la.only(node), room, observedLocal: link.local, observedRemote: link.remote, now: this.#now() });
    if (this.#link !== link) return;
    if (!r.ok) {
      this.#w1Failures += 1;
      trace('ATTACH_OPERATOR W1 refused the node', { reason: r.reason, signer: r.did, linkId: link.id, failures: this.#w1Failures });
      if (this.#w1Failures >= MAX_W1_FAILURES) {
        this.#stop();
        this.#setPhase('refused', { reason: 'w1-failed' });
        return;
      }
      this.#closeAndRetry();
      return;
    }
    this.#w1Failures = 0;
    if (this.#mode === 'pairing' && this.#invite) {
      const hello = await la.createPairHello(this.keys.app, {
        invite: this.#invite, local: link.local, remote: link.remote, operatorDid: this.keys.operator.did, issuedAt: this.#now(),
      });
      link.hello = hello;
      await this.#text(hello);
      this.pairing = { stage: 'confirm', code: await la.checkCode(hello) };
      this.#timers.clear(link.bindTimer); // the pairing waits for a person
      trace('ATTACH_OPERATOR pairing: node verified, hello sent', { node, linkId: link.id });
      this.#setPhase('pairing', { reason: null });
      return;
    }
    await this.#text(await la.createLinkBinding(this.keys.app, { room, local: link.local, remote: link.remote, issuedAt: this.#now() }));
    link.bound = true;
    this.#timers.clear(link.bindTimer);
    this.#clearUnreachable();
    this.#retries = 0;
    trace('ATTACH_OPERATOR bound', { node, app: this.keys.app.did, linkId: link.id });
    this.#setPhase('connected', { reason: null });
    for (const cb of this.#boundListeners) cb(link.id);
  }

  async #onRefused(link: Link, msg: CanonObject): Promise<void> {
    const node = this.nodeDid;
    if (!node) return;
    const r = await la.verifyRefusal(msg, { nodeDid: node, observedLocal: link.local, observedRemote: link.remote, now: this.#now() });
    if (!r.ok) {
      trace('ATTACH_OPERATOR an unverifiable refusal was ignored', { reason: r.reason });
      return;
    }
    trace('ATTACH_OPERATOR refused by the node', { refusal: r.refusal });
    if (this.#mode === 'pairing') {
      this.#stop();
      this.pairing = { stage: 'refused', code: this.pairing?.code ?? null };
      this.#setPhase('refused', { reason: r.refusal });
      return;
    }
    if (r.refusal === 'not-enrolled') {
      this.#stop();
      await this.#d.keystore.forgetPairing();
      this.record = null;
      this.#setPhase('unpaired', { reason: 'not-enrolled' });
      return;
    }
    if (r.refusal === 'busy') {
      this.#setPhase('busy', { reason: 'busy' });
      this.#closeAndRetry(15_000);
    }
  }

  async #onPaired(link: Link, msg: CanonObject): Promise<void> {
    const invite = this.#invite;
    if (this.#mode !== 'pairing' || !invite || !this.keys || this.pairing?.stage !== 'sent') return;
    const r = await la.verifyPaired(msg, {
      nodeDid: invite.nodeDid, appDid: this.keys.app.did, pairingId: invite.pairingId,
      observedLocal: link.local, observedRemote: link.remote, now: this.#now(),
    });
    if (!r.ok || !r.room) {
      trace('ATTACH_OPERATOR an unverifiable receipt was ignored', { reason: r.reason });
      return;
    }
    const record: PairingRecord = {
      v: 1, node: invite.nodeDid, room: r.room, app: this.keys.app.did, operator: r.operatorDid ?? '', pairedAt: this.#now(),
    };
    await this.#d.keystore.savePairing(record);
    this.#d.persist?.();
    this.record = record;
    this.#invite = null;
    this.pairing = null;
    trace('ATTACH_OPERATOR paired', { node: record.node, operatorEnrolled: !!r.operatorDid });
    this.#stop();
    this.#retries = 0;
    this.#joinTabs();
    this.#begin('standing');
  }

  // -- helpers ------------------------------------------------------------------------------

  async #text(obj: CanonObject): Promise<void> {
    await this.#d.peer.sendData(canonicalText(obj));
  }

  #dropLink(): void {
    if (this.#link) this.#timers.clear(this.#link.bindTimer);
    this.#link = null;
  }

  #stop(): void {
    this.#dropLink();
    this.#running = false;
    this.#timers.clear(this.#retryTimer);
    this.#retryTimer = null;
    this.#clearUnreachable();
    this.#d.peer.closePeerConnection();
  }

  #closeAndRetry(minMs = 0): void {
    this.#dropLink();
    this.#d.peer.closePeerConnection();
    this.#running = false;
    this.#retry(minMs);
  }

  #retry(minMs = 0): void {
    if (this.#stepAside || this.#mode === null) return;
    this.#timers.clear(this.#retryTimer);
    const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** this.#retries);
    const delay = Math.max(minMs, base * (0.8 + 0.4 * this.#random()));
    this.#retries += 1;
    if (this.phase === 'connected') this.#setPhase('reconnecting', { reason: null });
    this.#retryTimer = this.#timers.set(() => {
      this.#retryTimer = null;
      if (!this.#running) this.#connect();
    }, delay);
  }

  #armUnreachable(): void {
    if (this.#unreachableTimer !== null || this.#mode !== 'standing') return;
    this.#unreachableTimer = this.#timers.set(() => {
      this.#unreachableTimer = null;
      if (!this.#link?.bound && (this.phase === 'connecting' || this.phase === 'reconnecting')) {
        this.#setPhase('unreachable', { reason: null });
      }
    }, UNREACHABLE_AFTER_MS);
  }

  #clearUnreachable(): void {
    this.#timers.clear(this.#unreachableTimer);
    this.#unreachableTimer = null;
  }

  #joinTabs(): void {
    const tabs = this.#d.tabs;
    if (!tabs) return;
    tabs.onmessage = (ev) => {
      const m = ev.data as { type?: string; tab?: string } | null;
      if (m?.type === 'attaching' && m.tab !== this.#tabId && !this.#stepAside) {
        // the newest tab takes over; this one steps aside for good
        this.#stepAside = true;
        this.#stop();
        this.#setPhase('busy', { reason: 'another-tab' });
      }
    };
    tabs.postMessage({ type: 'attaching', tab: this.#tabId });
  }

  #setPhase(phase: LinkPhase, info: PhaseInfo): void {
    const same = phase === this.phase && info.reason === this.info.reason && phase !== 'pairing';
    this.phase = phase;
    this.info = info;
    if (same) return;
    trace(`ATTACH_OPERATOR phase ${phase}`, { reason: info.reason });
    for (const cb of this.#phaseListeners) cb(phase, info);
  }
}
