/**
 * A browser forwarder for the inference mesh: the browser-side counterpart of
 * `participant.Carrier` with the forward role only.
 *
 * It does four things and nothing else:
 *
 *  - **heartbeats** -- signs a `heartbeat/v2` every `heartbeatEvery` seconds,
 *    hash-linked to its previous one and witnessing the newest heartbeat each
 *    neighbour handed it directly, so full nodes count its links as witnessed;
 *  - **flooding** -- passes on each newly verified heartbeat, advisory, fault,
 *    incident, reset, lease revocation and admission to every other neighbour,
 *    each checked against the role that must have signed it;
 *  - **carrying** -- moves `req` frames toward the model node and `resp` /
 *    `nack` frames back toward the decider, only where the signed decision
 *    puts this node on the path, and turns a leg back (`nack`) when it cannot
 *    go on;
 *  - **Lockdown** -- while it holds an unreset mutiny incident it refuses to
 *    carry any decision that is not a Lockdown decision, and never carries for
 *    a latched decider or to a latched hop.
 *
 * It never decides, serves, answers as a model, holds a lease, or keeps state
 * beyond bounded in-memory tables; closing the page ends it. Admission makes
 * it forward-only on every full node anyway (`topology.LinkStateDB.forward_only`).
 *
 * Inbound frames are processed one at a time (verification is async), with a
 * per-neighbour rate cap, a size cap and a bounded queue.
 */

import { type CanonObject, type CanonValue, isCanonObject, parseLossless } from '../core/canon.ts';
import { type Signer } from '../core/ed25519.ts';
import {
  ADMISSION,
  ADVISORY,
  FAULT,
  GENESIS,
  HEAD,
  HEARTBEAT,
  type HeldIncident,
  INCIDENT,
  ISOLATE_ONLY,
  LEASE_REVOCATION,
  MAX_REASON,
  MODE_LOCKDOWN,
  type MeshTrust,
  RESET,
  type Trusts,
  type Witness,
  anyOf,
  checkAdmission,
  checkAdvisory,
  checkDecision,
  checkFault,
  checkHeartbeat,
  checkLeaseRevocation,
  checkRequest,
  checkResponse,
  digest,
  encodeFrame,
  isDid,
  isHex64,
  isObj,
  makeHeartbeat,
  num,
  readIncident,
  readReset,
} from './protocol.ts';

export const FLOODED: ReadonlySet<string> = new Set([HEARTBEAT, ADVISORY, FAULT, INCIDENT, RESET, LEASE_REVOCATION, ADMISSION]);

/** Where frames go out: the WebRTC layer, or a test double. */
export interface MeshLink {
  send(did: string, text: string): boolean;
}

export interface NodeConfig {
  readonly heartbeatEvery: number;   // seconds
  readonly maxNeighbors: number;
  readonly maxFrameChars: number;
  readonly frameRate: number;        // frames per second per neighbour, sustained
  readonly frameBurst: number;
  readonly maxQueue: number;         // inbound frames waiting for verification
}

export const DEFAULT_CONFIG: NodeConfig = {
  heartbeatEvery: 2,
  maxNeighbors: 8,
  maxFrameChars: 64 * 1024,
  frameRate: 100,
  frameBurst: 300,
  maxQueue: 512,
};

const SEEN = 8192;
const NODES = 1024;
const MAX_INCIDENTS = 256;
const MAX_CLEARED = 4096;
const MAX_RESETS = 64;
const DIR_INCIDENTS = 2;
const DIR_RESETS = 8;
const DIR_IDS = 64;
const DIR_ADMISSIONS = 32;
const INC_QUERY = 16;
const REASK_AFTER = 30;

/** A bounded insertion-ordered set: `add` is false for a key already held. */
class Recent {
  readonly #keys = new Set<string>();
  readonly #size: number;
  constructor(size: number) {
    this.#size = size;
  }
  add(key: string): boolean {
    if (this.#keys.has(key)) return false;
    this.#keys.add(key);
    if (this.#keys.size > this.#size) this.#keys.delete(this.#keys.values().next().value!);
    return true;
  }
  has(key: string): boolean {
    return this.#keys.has(key);
  }
}

function boundedSet<K, V>(map: Map<K, V>, key: K, value: V, size: number): void {
  map.delete(key);
  map.set(key, value);
  while (map.size > size) map.delete(map.keys().next().value!);
}

/** Verified, unexpired operator admissions, newest per subject (`admission.AdmissionBook`). */
export class AdmissionBook implements Trusts {
  readonly #bySubject = new Map<string, CanonObject>();
  readonly #capacity: number;
  readonly #clock: () => number;
  constructor(clock: () => number, capacity = 256) {
    this.#clock = clock;
    this.#capacity = capacity;
  }
  async add(obj: unknown, trust: MeshTrust, now: number): Promise<boolean> {
    if (await checkAdmission(obj, trust, now)) return false;
    const a = obj as CanonObject;
    const subject = a['subject'] as string;
    const held = this.#bySubject.get(subject);
    if (held !== undefined && num(held['expires_at']) >= num(a['expires_at'])) return false;
    boundedSet(this.#bySubject, subject, a, this.#capacity);
    return true;
  }
  has(did: string): boolean {
    const held = this.#bySubject.get(did);
    return held !== undefined && num(held['expires_at']) > this.#clock();
  }
  signed(): CanonObject[] {
    const now = this.#clock();
    return [...this.#bySubject.values()].filter((a) => num(a['expires_at']) > now);
  }
  prune(now: number): void {
    for (const [d, a] of this.#bySubject) if (num(a['expires_at']) <= now) this.#bySubject.delete(d);
  }
}

/**
 * The incidents and operator resets a browser holds. Lockdown is any open
 * target of a mutiny incident (any kind but `node.heartbeat-fork`); only an
 * operator reset naming that `(target, incident)` pair clears it.
 */
export class Ledger {
  readonly #incidents = new Map<string, HeldIncident>();
  readonly #tombstones = new Recent(MAX_INCIDENTS * 4);
  readonly #cleared = new Map<string, true>();
  readonly #resets = new Map<string, CanonObject>();

  #open(inc: HeldIncident): string[] {
    return [...inc.targets].filter((t) => !this.#cleared.has(`${t}|${inc.id}`));
  }

  add(inc: HeldIncident): boolean {
    if (this.#incidents.has(inc.id) || this.#tombstones.has(inc.id)) return false;
    if (this.#open(inc).length === 0) {
      this.#tombstones.add(inc.id); // arrived after the reset that cleared it
      return true;
    }
    if (this.#incidents.size >= MAX_INCIDENTS) return false;
    this.#incidents.set(inc.id, inc);
    return true;
  }

  reset(key: string, signed: CanonObject, targets: readonly string[], ids: readonly string[]): boolean {
    if (this.#resets.has(key)) return false;
    boundedSet(this.#resets, key, signed, MAX_RESETS);
    for (const t of targets) for (const i of ids) boundedSet(this.#cleared, `${t}|${i}`, true as const, MAX_CLEARED);
    for (const i of ids) {
      const inc = this.#incidents.get(i);
      if (inc !== undefined && this.#open(inc).length === 0) {
        this.#incidents.delete(i);
        this.#tombstones.add(i);
      }
    }
    return true;
  }

  get lockdown(): boolean {
    for (const inc of this.#incidents.values()) if (!ISOLATE_ONLY.has(inc.kind) && this.#open(inc).length) return true;
    return false;
  }

  excludesNode(did: string): boolean {
    for (const inc of this.#incidents.values()) {
      const open = this.#open(inc);
      if (open.includes(`node:${did}`) || open.includes(`decider:${did}`)) return true;
    }
    return false;
  }

  knows(id: string): boolean {
    return this.#incidents.has(id) || this.#tombstones.has(id);
  }

  incident(id: string): HeldIncident | undefined {
    return this.#incidents.get(id);
  }

  heldIds(): string[] {
    return [...this.#incidents.keys()].sort();
  }

  signedIncidents(): CanonObject[] {
    return [...this.#incidents.values()].map((i) => i.signed);
  }

  signedResets(): CanonObject[] {
    return [...this.#resets.values()];
  }
}

export interface NodeStats {
  readonly did: string;
  readonly mode: 'normal' | 'lockdown';
  readonly neighbors: readonly string[];
  readonly carried: number;
  readonly refused: number;
  readonly flooded: number;
  readonly dropped: number;
  readonly beats: number;
}

export interface NodeEvent {
  readonly at: number;
  readonly what: string;
  readonly detail: string;
}

interface Bucket {
  tokens: number;
  at: number;
}

/** Seconds since the epoch, as `time.time()`. */
export const wallClock = (): number => Date.now() / 1000;

export class BrowserNode {
  readonly did: string;
  readonly trust: MeshTrust;
  readonly admissions: AdmissionBook;
  readonly ledger = new Ledger();
  readonly config: NodeConfig;
  readonly neighbors = new Set<string>();
  readonly events: NodeEvent[] = [];

  readonly #signer: Signer;
  readonly #clock: () => number;
  #link: MeshLink | null = null;
  readonly #seen = new Recent(SEEN);
  readonly #newest = new Map<string, bigint>();               // node -> newest heartbeat seq absorbed
  readonly #verifiedHb = new Map<string, bigint>();           // heartbeat digest -> seq (verified ones)
  readonly #direct = new Map<string, { seq: bigint; digest: string }>();
  readonly #advisories = new Map<string, bigint>();           // coordinator -> newest epoch
  readonly #asked = new Map<string, number>();
  readonly #buckets = new Map<string, Bucket>();
  #seq: bigint;
  #prev = GENESIS;
  #nextBeat = 0;
  #queue: Promise<void> = Promise.resolve();
  #pending = 0;
  #stopped = false;
  #counts = { carried: 0, refused: 0, flooded: 0, dropped: 0, beats: 0 };

  /**
   * @param trust the DID lists of the operator's join bundle; `mesh` here is
   *   the static list -- admitted DIDs (other browsers) are added from
   *   verified admissions.
   */
  constructor(signer: Signer, trust: MeshTrust, opts: { clock?: () => number; config?: Partial<NodeConfig> } = {}) {
    this.#signer = signer;
    this.did = signer.did;
    this.#clock = opts.clock ?? wallClock;
    this.config = { ...DEFAULT_CONFIG, ...opts.config };
    this.admissions = new AdmissionBook(this.#clock);
    this.trust = { ...trust, mesh: anyOf(trust.mesh, this.admissions) };
    // Like Python's time_ns() seed: keeps increasing across page loads.
    this.#seq = BigInt(Date.now()) * 1_000_000n;
  }

  attach(link: MeshLink): void {
    this.#link = link;
  }

  // -- links ----------------------------------------------------------------------------------

  /** A DataChannel to `did` passed the DID <-> DTLS handshake. */
  bind(did: string): boolean {
    if (this.#stopped || did === this.did || !this.trust.mesh.has(did)) return false;
    if (!this.neighbors.has(did) && this.neighbors.size >= this.config.maxNeighbors) return false;
    this.neighbors.add(did);
    this.#note('link', did);
    this.#send(did, 'dir?'); // learn the incidents, resets and admissions the mesh holds
    this.#nextBeat = 0;      // announce the link with the next tick
    return true;
  }

  unbind(did: string): void {
    if (this.neighbors.delete(did)) {
      this.#direct.delete(did);
      this.#buckets.delete(did);
      this.#note('unlink', did);
    }
  }

  stop(): void {
    this.#stopped = true;
    for (const d of [...this.neighbors]) this.unbind(d);
  }

  get mode(): 'normal' | 'lockdown' {
    return this.ledger.lockdown ? 'lockdown' : 'normal';
  }

  stats(): NodeStats {
    return { did: this.did, mode: this.mode, neighbors: [...this.neighbors].sort(), ...this.#counts };
  }

  // -- inbound --------------------------------------------------------------------------------

  /** An inbound frame from a bound neighbour. Resolves when it was processed. */
  handle(from: string, text: string): Promise<void> {
    if (this.#stopped || !this.neighbors.has(from)) return Promise.resolve();
    if (text.length > this.config.maxFrameChars || !this.#admit(from) || this.#pending >= this.config.maxQueue) {
      this.#counts.dropped++;
      return Promise.resolve();
    }
    this.#pending++;
    const run = this.#queue.then(async () => {
      try {
        await this.#handle(from, text);
      } catch (e) {
        this.#note('error', String(e));
      } finally {
        this.#pending--;
      }
    });
    this.#queue = run;
    return run;
  }

  /** Heartbeat when due, prune; call it every few hundred milliseconds. */
  tick(): Promise<void> {
    const run = this.#queue.then(async () => {
      if (this.#stopped) return;
      const now = this.#clock();
      if (now >= this.#nextBeat) {
        await this.#beat(now);
        this.#nextBeat = now + this.config.heartbeatEvery;
      }
      this.admissions.prune(now);
    });
    this.#queue = run.catch((e) => this.#note('error', String(e)));
    return this.#queue;
  }

  /** Apply an admission (this node's own, from the bundle) and flood it. */
  async publish(obj: CanonObject): Promise<boolean> {
    const d = await digest(obj);
    this.#seen.add(d);
    const ok = await this.#absorb(obj, null, d);
    if (ok) this.#flood(obj, null);
    return ok;
  }

  #admit(from: string): boolean {
    const now = this.#clock();
    const cfg = this.config;
    const b = this.#buckets.get(from) ?? { tokens: cfg.frameBurst, at: now };
    b.tokens = Math.min(cfg.frameBurst, b.tokens + (now - b.at) * cfg.frameRate);
    b.at = now;
    this.#buckets.set(from, b);
    if (b.tokens < 1) return false;
    b.tokens -= 1;
    return true;
  }

  async #handle(from: string, text: string): Promise<void> {
    let f: CanonValue;
    try {
      f = parseLossless(text, { maxLength: this.config.maxFrameChars });
    } catch {
      return;
    }
    if (!isCanonObject(f) || typeof f['k'] !== 'string') return;
    switch (f['k']) {
      case 'flood': return this.#onFlood(from, f);
      case 'req': return this.#onReq(from, f);
      case 'resp': return this.#onResp(from, f);
      case 'nack': return this.#onNack(from, f);
      case 'dir?': return this.#onDirQuery(from);
      case 'dir': return this.#onDir(from, f);
      case 'inc?': return this.#onIncQuery(from, f);
      default: return; // submit, result, refused, grant: not for a forwarder
    }
  }

  // -- flooding -------------------------------------------------------------------------------

  #send(to: string, kind: string, fields: Record<string, CanonValue> = {}): boolean {
    return this.#link !== null && this.#link.send(to, encodeFrame(kind, fields));
  }

  #flood(obj: CanonObject, exclude: string | null): void {
    for (const d of [...this.neighbors].sort()) if (d !== exclude) this.#send(d, 'flood', { obj });
    this.#counts.flooded++;
  }

  async #onFlood(src: string, f: CanonObject): Promise<void> {
    const obj = f['obj'];
    if (!isObj(obj) || !FLOODED.has(obj['type'] as string)) return;
    const d = await digest(obj);
    const direct = obj['type'] === HEARTBEAT && obj['node'] === src;
    if (!this.#seen.add(d)) {
      if (direct) this.#heardDirectly(src, d);
      return;
    }
    if (await this.#absorb(obj, src, d)) {
      if (direct) this.#heardDirectly(src, d);
      this.#flood(obj, src);
    }
  }

  #heardDirectly(node: string, d: string): void {
    const seq = this.#verifiedHb.get(d);
    if (seq !== undefined && seq > (this.#direct.get(node)?.seq ?? 0n)) this.#direct.set(node, { seq, digest: d });
  }

  /** Verify and apply one flooded object; true when it is new and so travels on. */
  async #absorb(obj: CanonObject, src: string | null, d: string): Promise<boolean> {
    const now = this.#clock();
    switch (obj['type']) {
      case HEARTBEAT: {
        if (await checkHeartbeat(obj, this.trust, now)) return false;
        const node = obj['node'] as string;
        const seq = BigInt(obj['seq'] as number | bigint);
        if (seq <= (this.#newest.get(node) ?? -1n)) return false;
        boundedSet(this.#newest, node, seq, NODES);
        boundedSet(this.#verifiedHb, d, seq, NODES * 4);
        return true;
      }
      case ADVISORY: {
        if (await checkAdvisory(obj, this.trust, now)) return false;
        const who = obj['coordinator'] as string;
        const epoch = BigInt(obj['epoch'] as number | bigint);
        if (epoch <= (this.#advisories.get(who) ?? -1n)) return false;
        boundedSet(this.#advisories, who, epoch, 64);
        return true;
      }
      case FAULT:
        return (await checkFault(obj, this.trust, now)) === null;
      case LEASE_REVOCATION:
        return (await checkLeaseRevocation(obj, this.trust)) === null;
      case ADMISSION:
        return this.admissions.add(obj, this.trust, now);
      case INCIDENT: {
        const inc = await readIncident(obj, this.trust);
        if (inc === null || !this.ledger.add(inc)) return false;
        this.#note('incident', `${inc.kind} ${[...inc.targets].join(' ')}`);
        return true;
      }
      case RESET: {
        const parsed = await readReset(obj, this.trust);
        if (parsed === null || !this.ledger.reset(d, obj, parsed[0], parsed[1])) return false;
        this.#note('reset', parsed[0].join(' '));
        return true;
      }
      default:
        void src;
        return false;
    }
  }

  async #beat(now: number): Promise<void> {
    this.#seq += 1n;
    const witness = new Map<string, Witness>();
    for (const n of this.neighbors) {
      const w = this.#direct.get(n);
      witness.set(n, w === undefined ? { seq: 0n, head: '' } : { seq: w.seq, head: w.digest.slice(0, HEAD) });
    }
    const hb = await makeHeartbeat(this.#signer, { seq: this.#seq, prev: this.#prev, neighbors: witness, issuedAt: now });
    this.#prev = await digest(hb);
    this.#seen.add(this.#prev);
    this.#counts.beats++;
    this.#flood(hb, null);
  }

  // -- the directory ---------------------------------------------------------------------------

  #onDirQuery(src: string): void {
    this.#send(src, 'dir', {
      leases: [],
      incidents: this.ledger.signedIncidents().slice(-DIR_INCIDENTS),
      resets: this.ledger.signedResets().slice(-DIR_RESETS),
      incident_ids: this.ledger.heldIds().slice(-DIR_IDS),
      admissions: this.admissions.signed().slice(-DIR_ADMISSIONS),
    });
  }

  async #onDir(src: string, f: CanonObject): Promise<void> {
    const list = (k: string): CanonValue[] => (Array.isArray(f[k]) ? [...(f[k] as CanonValue[])] : []);
    for (const obj of [...list('incidents'), ...list('resets'), ...list('admissions')].slice(0, 64)) {
      if (!isObj(obj)) continue;
      const d = await digest(obj);
      if (this.#seen.add(d)) await this.#absorb(obj, src, d);
    }
    const now = this.#clock();
    const want: string[] = [];
    for (const i of list('incident_ids').slice(0, DIR_IDS)) {
      if (!isHex64(i) || this.ledger.knows(i) || now - (this.#asked.get(i) ?? -REASK_AFTER) < REASK_AFTER) continue;
      want.push(i);
      boundedSet(this.#asked, i, now, 1024);
      if (want.length === INC_QUERY) break;
    }
    if (want.length) this.#send(src, 'inc?', { ids: want });
  }

  #onIncQuery(src: string, f: CanonObject): void {
    const ids = Array.isArray(f['ids']) ? (f['ids'] as CanonValue[]).slice(0, INC_QUERY) : [];
    for (const i of ids) {
      const inc = isHex64(i) ? this.ledger.incident(i) : undefined;
      if (inc !== undefined) this.#send(src, 'flood', { obj: inc.signed });
    }
  }

  // -- carrying legs ----------------------------------------------------------------------------

  /** The leg a frame travels on, if it is where the signed decision puts this node (`Carrier._leg`). */
  #leg(src: string, decision: CanonObject, legI: CanonValue | undefined, hop: CanonValue | undefined,
       towardProvider: boolean): CanonObject | null {
    const legs = decision['legs'] as CanonObject[];
    if (typeof legI !== 'number' || typeof hop !== 'number' || !Number.isSafeInteger(legI) || !Number.isSafeInteger(hop)) {
      return null;
    }
    if (legI < 0 || legI >= legs.length) return null;
    const path = legs[legI]!['path'] as string[];
    if (hop < 0 || hop >= path.length || path[hop] !== this.did) return null;
    const ok = towardProvider ? hop >= 1 && src === path[hop - 1] : hop <= path.length - 2 && src === path[hop + 1];
    return ok ? legs[legI]! : null;
  }

  /** Why this node will not carry a leg of `decision` now, or null (`Carrier.may_carry`). */
  mayCarry(decision: CanonObject): string | null {
    if (this.ledger.excludesNode(decision['decider'] as string)) return 'the decider is latched';
    if (this.ledger.lockdown && decision['mode'] !== MODE_LOCKDOWN) return 'lockdown: the decision layer is suspended';
    return null;
  }

  async #onReq(src: string, f: CanonObject): Promise<void> {
    const now = this.#clock();
    const request = f['request'];
    const decision = f['decision'];
    if (await checkRequest(request, this.trust, now)) return;
    if (await checkDecision(decision, this.trust, request as CanonObject)) return;
    const d = decision as CanonObject;
    const leg = this.#leg(src, d, f['leg'], f['hop'], true);
    if (leg === null) return;
    const hop = f['hop'] as number;
    const path = leg['path'] as string[];
    if (hop === path.length - 1) return; // this node is never a model node
    const next = path[hop + 1]!;
    let why = this.mayCarry(d);
    if (why === null && this.ledger.excludesNode(next)) why = 'the next hop is latched';
    if (why === null && !this.#send(next, 'req', { request: request as CanonObject, decision: d, leg: f['leg']!, hop: hop + 1 })) {
      why = 'the next hop is unreachable';
    }
    if (why === null) {
      this.#counts.carried++;
      this.#note('carry', `req hop ${hop} -> ${next.slice(-8)}`);
      return;
    }
    this.#counts.refused++;
    this.#note('nack', why);
    this.#send(path[hop - 1]!, 'nack', {
      decision: d, leg: f['leg']!, hop: hop - 1, at: this.did, unreachable: next, reason: why.slice(0, MAX_REASON),
    });
  }

  async #onNack(src: string, f: CanonObject): Promise<void> {
    const decision = f['decision'];
    if (await checkDecision(decision, this.trust)) return;
    const d = decision as CanonObject;
    const leg = this.#leg(src, d, f['leg'], f['hop'], false);
    const hop = f['hop'] as number;
    if (leg === null || hop === 0) return; // hop 0 is the decider
    const unreachable = isDid(f['unreachable']) ? f['unreachable'] : src;
    const at = isDid(f['at']) ? f['at'] : src;
    const reason = typeof f['reason'] === 'string' ? f['reason'].slice(0, MAX_REASON) : '';
    this.#send((leg['path'] as string[])[hop - 1]!, 'nack', { decision: d, leg: f['leg']!, hop: hop - 1, at, unreachable, reason });
  }

  async #onResp(src: string, f: CanonObject): Promise<void> {
    const decision = f['decision'];
    const response = f['response'];
    if (await checkDecision(decision, this.trust)) return;
    const d = decision as CanonObject;
    if (await checkResponse(response, this.trust, d)) return;
    const leg = this.#leg(src, d, f['leg'], f['hop'], false);
    const hop = f['hop'] as number;
    if (leg === null || hop === 0 || leg['provider'] !== (response as CanonObject)['provider']) return;
    const prev = (leg['path'] as string[])[hop - 1]!;
    if (this.#send(prev, 'resp', { decision: d, response: response as CanonObject, leg: f['leg']!, hop: hop - 1 })) {
      this.#counts.carried++;
      this.#note('carry', `resp hop ${hop} -> ${prev.slice(-8)}`);
    }
  }

  #note(what: string, detail: string): void {
    this.events.push({ at: this.#clock(), what, detail });
    if (this.events.length > 200) this.events.splice(0, this.events.length - 200);
  }
}
