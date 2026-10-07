/**
 * The operator end of `secdogie/dialogue/v1`, as the page's {@link CoreLink}.
 *
 * Under it: a bound link (net/attach.ts, or the demo's in-page node), DID-signed
 * direct frames (net/frame.ts), the `dialogue/v1` mux channel, and one dialogue
 * session for the page's life (net/session.ts). Over it: the conversation, in
 * words (voice/*).
 *
 *  - ADD_GOAL: what the person types becomes a `control` add_goal.
 *  - CURRENT_STATUS: the node's status lines say whether work is running or a
 *    step waits for the operator; "Stop" stops whatever goal is running.
 *  - Gate 1: a node's question becomes a card; the answer goes back verbatim.
 *  - Gate 2: a challenge goes through `Gate2SignerFlow` -- reviewed against the
 *    action shown, armed after a moment on screen, signed only inside the
 *    person's click (and only if the node enrolled this browser's operator key),
 *    self-checked, sent. "Sent" is all the page claims until the node reports
 *    how the goal ended; a response that may not have arrived is "uncertain",
 *    never "did not happen".
 *  - A dropped link changes nothing on the node: open cards wait ("when you are
 *    back"), the node shows them again on HELLO, and only expiry closes them.
 *  - Memory offers and structural snapshots go to DevTools only.
 */

import type { Signer } from '../core/ed25519.ts';
import { TrustSet } from '../core/envelope.ts';
import { trace } from '../core/trace.ts';
import { type Gate2Bubble, Gate2SignerFlow, type OperatorKeyring, UserGestureKeyring } from '../gate2/signer_flow.ts';
import {
  ControlOp,
  DialogueType,
  type Gate2ChallengePacket,
  type Packet,
  PacketKind,
  SessionEvent,
  dialoguePacket,
  randomHex,
} from '../gate2/wire.ts';
import type { LinkPhase, PairingView, PhaseInfo } from '../net/attach.ts';
import { DirectFrames } from '../net/frame.ts';
import type { Keys } from '../net/keystore.ts';
import { DIALOGUE_CHANNEL, muxDecode, muxEncode } from '../net/mux.ts';
import { DialogueSession, type Delivered } from '../net/session.ts';
import { consentView } from '../voice/consent.ts';
import type { Dict } from '../voice/dict.ts';
import { askView } from '../voice/question.ts';
import { readStatus, statusSentence } from '../voice/status.ts';
import type { Activity, ConsentState, CoreLink, Presence, Turn } from './core_link.ts';
import { Conversation } from './conversation.ts';

/** What the client needs from the link below it (net/attach.ts `Attachment` is one). */
export interface FrameLink {
  readonly phase: LinkPhase;
  readonly info: PhaseInfo;
  readonly pairing: PairingView | null;
  readonly keys: Keys | null;
  readonly nodeDid: string | null;
  readonly canApprove: boolean;
  onPhase(cb: (phase: LinkPhase, info: PhaseInfo) => void): () => void;
  onFrame(cb: (data: Uint8Array<ArrayBuffer>) => void): () => void;
  onBound(cb: (linkId: number) => void): () => void;
  send(data: Uint8Array): Promise<boolean>;
  confirmPairing(): Promise<boolean>;
  cancelPairing(): void;
}

export interface OperatorClientOptions {
  readonly link: FrameLink;
  readonly voice: Dict;
  /** How the operator key is unlocked; by default only inside a real click. */
  readonly keyringFor?: (operator: Signer) => OperatorKeyring;
  /** Whether this call runs inside a real user activation (Approve / Deny answers). */
  readonly activation?: () => boolean;
  /** Wall-clock seconds. */
  readonly clock?: () => number;
  readonly armingDelay?: number;
  readonly timers?: {
    set: (fn: () => void, ms: number) => unknown;
    clear: (h: unknown) => void;
    every: (fn: () => void, ms: number) => unknown;
    cancel: (h: unknown) => void;
  };
}

const RESEND_GRACE_MS = 6_000;

export class OperatorClient implements CoreLink {
  readonly conv = new Conversation();
  readonly #o: OperatorClientOptions;
  readonly #d: Dict;
  readonly #clock: () => number;
  readonly #arming: number;
  readonly #timers: NonNullable<OperatorClientOptions['timers']>;
  #presence: Presence;
  #activity: Activity & { goal: string } = { running: false, waiting: false, goal: '' };
  #frames: DirectFrames | null = null;
  #session: DialogueSession | null = null;
  #flow: Gate2SignerFlow | null = null;
  readonly #probes = new Map<string, string>(); // probe id -> turn id
  readonly #challenges = new Map<string, string>(); // challenge id -> turn id
  readonly #requests = new Map<string, { op: string; goal: string }>(); // request id -> what it was
  readonly #myGoals = new Set<string>();
  #pairingTurn: string | null = null;
  #boundAt = 0;
  #saidStillRunning = false;
  readonly #listeners = new Set<() => void>();
  #ticker: unknown = null;

  constructor(o: OperatorClientOptions) {
    this.#o = o;
    this.#d = o.voice;
    this.#clock = o.clock ?? (() => Date.now() / 1000);
    this.#arming = o.armingDelay ?? 0.8;
    this.#timers = o.timers ?? {
      set: (fn, ms) => setTimeout(fn, ms),
      clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
      every: (fn, ms) => setInterval(fn, ms),
      cancel: (h) => clearInterval(h as ReturnType<typeof setInterval>),
    };
    this.#presence = { phase: o.link.phase, reason: o.link.info.reason };
    this.conv.onChange(() => this.#changed());
    o.link.onPhase((phase, info) => this.#onPhase(phase, info));
    o.link.onFrame((raw) => void this.#onFrame(raw));
    o.link.onBound(() => void this.#onBound());
    this.#ticker = this.#timers.every(() => this.#expire(), 1000);
    this.#onPhase(o.link.phase, o.link.info);
  }

  // -- CoreLink -------------------------------------------------------------------

  get presence(): Presence {
    return this.#presence;
  }

  get activity(): Activity {
    return { running: this.#activity.running, waiting: this.#activity.waiting || this.#openCards() > 0 };
  }

  get replyTo(): string | null {
    const open = [...this.conv.list()].reverse().find((t) => t.kind === 'ask' && t.state === 'open');
    return open?.id ?? null;
  }

  turns(): readonly Turn[] {
    return this.conv.list();
  }

  onChange(cb: () => void): () => void {
    this.#listeners.add(cb);
    return () => this.#listeners.delete(cb);
  }

  now(): number {
    return this.#clock();
  }

  say(text: string): void {
    const t = text.trim();
    if (!t) return;
    this.conv.add({ kind: 'you', text: t });
    if (!this.#session || !this.#online()) {
      this.conv.add({ kind: 'say', text: this.#d.reply.undelivered, tone: 'problem' });
      return;
    }
    const goal = `g-${randomHex(6)}`;
    const request = `r-${randomHex(6)}`;
    this.#requests.set(request, { op: ControlOp.AddGoal, goal });
    this.#myGoals.add(goal);
    trace('ADD_GOAL', { goal, request });
    void this.#send({
      kind: PacketKind.Control,
      packet: { request_id: request, op: ControlOp.AddGoal, goal_id: goal, title: t, memory_id: '', confirmation: {} },
    });
  }

  answer(turnId: string, value: string): void {
    const turn = this.conv.get(turnId);
    if (turn?.kind !== 'ask' || turn.state !== 'open' || !value.trim()) return;
    const probeId = [...this.#probes].find(([, id]) => id === turnId)?.[0];
    if (!probeId || !this.#session) return;
    if ((value === 'Approve' || value === 'Deny') && this.#o.activation && !this.#o.activation()) {
      trace('an Approve/Deny answer outside a user activation was not sent');
      return;
    }
    const label = turn.options.find((o) => o.value === value)?.label ?? value;
    this.conv.update(turnId, { state: 'answered', answer: label });
    void this.#send({
      kind: PacketKind.Dialogue,
      packet: dialoguePacket({ probe_id: `a-${randomHex(6)}`, dialogue_type: DialogueType.UserClarification, content: value, in_reply_to: probeId }),
    });
  }

  async approve(turnId: string): Promise<void> {
    const cid = this.#challengeOf(turnId);
    const turn = this.conv.get(turnId);
    if (!cid || !this.#flow || turn?.kind !== 'consent' || turn.state !== 'awaiting' || !turn.canApprove || turn.tooLong) return;
    const r = await this.#flow.approve(cid, this.#clock());
    if (!r.ok) trace('Gate 2 approve did not go through', { reason: r.reason, challenge: cid });
  }

  cancel(turnId: string): void {
    const cid = this.#challengeOf(turnId);
    if (!cid || !this.#flow) return;
    void this.#flow.deny(cid, this.#clock());
  }

  stop(): void {
    const goal = this.#activity.goal;
    if (!goal || !this.#session) return;
    const request = `r-${randomHex(6)}`;
    this.#requests.set(request, { op: ControlOp.Stop, goal });
    trace('stop requested', { goal, request });
    void this.#send({ kind: PacketKind.Control, packet: { request_id: request, op: ControlOp.Stop, goal_id: goal, title: '', memory_id: '', confirmation: {} } });
  }

  async confirmPairing(): Promise<void> {
    await this.#o.link.confirmPairing();
    this.#syncPairing();
  }

  cancelPairing(): void {
    this.#o.link.cancelPairing();
  }

  hold(on: boolean): void {
    this.conv.hold(on);
    if (!on) this.rearm();
  }

  rearm(): void {
    const now = this.#clock();
    for (const t of this.conv.list()) {
      if (t.kind === 'consent' && t.state === 'awaiting') {
        const cid = this.#challengeOf(t.id);
        if (cid && this.#flow) {
          this.#flow.veil(cid);
          this.#flow.unveil(cid, now);
        }
        this.conv.update(t.id, { armedAt: now + this.#arming });
      }
    }
  }

  /** Stop the client's own timers (tests; a page simply goes away). */
  dispose(): void {
    this.#timers.cancel(this.#ticker);
    this.#session?.stop();
  }

  // -- the link -------------------------------------------------------------------------

  #onPhase(phase: LinkPhase, info: PhaseInfo): void {
    this.#presence = { phase, reason: info.reason };
    if (!this.#online()) {
      for (const t of this.conv.list()) {
        if (t.kind === 'ask' && t.state === 'open') this.conv.update(t.id, { state: 'waiting' });
        if (t.kind === 'consent' && t.state === 'awaiting') this.conv.update(t.id, { state: 'waiting' });
      }
    }
    this.#syncPairing();
    this.#changed();
  }

  #syncPairing(): void {
    const view = this.#o.link.pairing;
    const phase = this.#presence.phase;
    if ((phase === 'pairing' || (phase === 'refused' && view)) && view) {
      if (this.#pairingTurn === null) this.#pairingTurn = this.conv.add({ kind: 'pairing', stage: view.stage, code: view.code });
      else this.conv.update(this.#pairingTurn, { stage: view.stage, code: view.code });
    } else if (this.#pairingTurn !== null) {
      this.conv.remove(this.#pairingTurn);
      this.#pairingTurn = null;
    }
  }

  async #onBound(): Promise<void> {
    const link = this.#o.link;
    const keys = link.keys;
    const node = link.nodeDid;
    if (!keys || !node) return;
    if (!this.#session) this.#open(keys, node);
    this.#boundAt = this.#clock();
    this.#saidStillRunning = false;
    trace('ATTACH_OPERATOR hello', { node });
    await this.#send({ kind: PacketKind.Session, packet: { event: SessionEvent.Hello, note: '' } });
    // Whatever the node still has open it shows again now; what it does not was settled meanwhile.
    this.#timers.set(() => this.#settleUnresent(), RESEND_GRACE_MS);
  }

  #open(keys: Keys, node: string): void {
    const trust = new TrustSet([node]);
    const frames = new DirectFrames({ self: keys.app, peerDid: node, trust });
    const session = new DialogueSession({
      self: keys.app,
      peerDid: node,
      trust,
      send: (f) => {
        void frames.build(muxEncode(DIALOGUE_CHANNEL, f)).then((raw) => this.#o.link.send(raw));
      },
    });
    session.onEnvelope = (d) => void this.#onEnvelope(d);
    session.onUndeliverable = (_id, p) => this.#undeliverable(p);
    session.onPeerDown = () => trace('the node went quiet');
    session.onPeerUp = () => trace('the node is heard again');
    session.start(100);
    const keyring = (this.#o.keyringFor ?? ((op: Signer) => new UserGestureKeyring(op)))(keys.operator);
    const flow = new Gate2SignerFlow({
      peerDid: node,
      keyring,
      clock: this.#clock,
      armingDelaySeconds: this.#arming,
      send: async (r) => {
        await this.#send({ kind: PacketKind.Gate2Response, packet: r });
      },
    });
    flow.onChange = (b) => this.#onBubble(b);
    this.#frames = frames;
    this.#session = session;
    this.#flow = flow;
  }

  async #onFrame(raw: Uint8Array<ArrayBuffer>): Promise<void> {
    const frames = this.#frames;
    const session = this.#session;
    if (!frames || !session) return;
    const data = await frames.open(raw);
    if (data === null) return;
    const m = muxDecode(data);
    if (m?.channel !== DIALOGUE_CHANNEL) return;
    await session.receive(m.payload);
  }

  async #send(p: Packet): Promise<void> {
    if (!this.#session) return;
    try {
      await this.#session.send(p);
    } catch (e) {
      trace('a packet could not be sealed', { error: (e as Error).message, kind: p.kind });
    }
  }

  // -- from the node --------------------------------------------------------------------

  async #onEnvelope({ packet }: Delivered): Promise<void> {
    switch (packet.kind) {
      case PacketKind.Dialogue: {
        const d = packet.packet;
        if (d.dialogue_type === DialogueType.SystemStatus) this.#onStatus(d.content, d.in_reply_to);
        else if (d.dialogue_type === DialogueType.SocraticQuestion) this.#onQuestion(d);
        return;
      }
      case PacketKind.Gate2Challenge:
        await this.#onChallenge(packet.packet);
        return;
      case PacketKind.MemoryCandidate:
        trace('memory offered (kept quarantined; not shown on the page)', { memory: packet.packet.memory_id, mclass: packet.packet.mclass });
        return;
      case PacketKind.StateSnapshot:
        trace('state_snapshot admitted and dropped (an AX window view; GRAPH_SNAPSHOT is not served by this node)');
        return;
      default:
        trace('ignored a packet', { kind: packet.kind });
    }
  }

  #onStatus(content: string, about: string): void {
    const s = readStatus(content, about);
    const req = about ? this.#requests.get(about) : undefined;
    if (s.kind === 'current') {
      trace('CURRENT_STATUS', { running: s.running, waiting: s.waiting, goal: s.goal });
      this.#activity = { running: s.running, waiting: s.waiting, goal: s.goal };
      if (s.running && !this.#saidStillRunning && this.#clock() - this.#boundAt < 3 && !this.#myGoals.has(s.goal)) {
        this.#saidStillRunning = true;
        this.conv.add({ kind: 'say', text: this.#d.reply.stillRunning, tone: 'quiet' });
      }
      this.#changed();
      return;
    }
    if (s.kind === 'answer-adopted' || s.kind === 'answer-expired') {
      const turn = this.#probes.get(about);
      if (turn && s.kind === 'answer-expired') this.conv.update(turn, { state: 'expired' });
    }
    if (s.kind === 'finished') {
      trace('goal finished', { goal: s.goal, ok: s.ok, detail: s.detail });
      for (const t of this.conv.list()) {
        if (t.kind === 'consent' && (t.state === 'sent' || t.state === 'uncertain')) this.conv.update(t.id, { state: s.ok ? 'done' : 'not-done' });
      }
      if (s.goal === this.#activity.goal) this.#activity = { running: false, waiting: false, goal: '' };
    }
    if (req) {
      this.#requests.delete(about);
      trace('request answered', { request: about, op: req.op, detail: content });
    } else if (s.kind === 'refused' || s.kind === 'other') {
      trace('node status', { detail: content });
    }
    const line = statusSentence(this.#d, s);
    if (line) this.conv.add({ kind: 'say', text: line.text, tone: line.tone });
    this.#changed();
  }

  #onQuestion(d: Parameters<typeof askView>[1]): void {
    const existing = this.#probes.get(d.probe_id);
    if (existing) {
      const t = this.conv.get(existing);
      if (t?.kind === 'ask' && t.state === 'waiting') this.conv.update(existing, { state: 'open' });
      return;
    }
    trace('Gate 1 question', { probe: d.probe_id, finding: d.gate_finding || null });
    const v = askView(this.#d, d);
    this.#probes.set(d.probe_id, this.conv.add({ kind: 'ask', ...v, state: 'open', answer: null }));
  }

  async #onChallenge(c: Gate2ChallengePacket): Promise<void> {
    if (!this.#flow) return;
    trace('Gate 2 challenge', {
      challenge: c.challenge_id, action_hash: c.action_hash, subject: c.subject_did, risk: c.risk_level,
      risk_explanation: c.risk_explanation, expires_at: c.expires_at,
    });
    const existing = this.#challenges.get(c.challenge_id);
    if (existing) {
      const t = this.conv.get(existing);
      if (t?.kind === 'consent' && t.state === 'waiting') {
        const now = this.#clock();
        this.#flow.veil(c.challenge_id);
        this.#flow.unveil(c.challenge_id, now);
        this.conv.update(existing, { state: 'awaiting', armedAt: now + this.#arming });
      }
      return;
    }
    try {
      await this.#flow.present(c, this.#clock());
    } catch (e) {
      trace('a repeated challenge was ignored', { error: (e as Error).message });
    }
  }

  #onBubble(b: Gate2Bubble): void {
    let turnId = this.#challenges.get(b.id);
    if (turnId === undefined) {
      const v = consentView(this.#d, b.challenge.target_action);
      if (b.state === 'refused') trace('Gate 2 review refused the challenge', { problems: b.review.problems });
      turnId = this.conv.add({
        kind: 'consent',
        sentence: v.sentence,
        quote: v.quote,
        grave: v.grave,
        tooLong: v.tooLong,
        state: b.state === 'refused' ? 'refused' : 'awaiting',
        armedAt: b.armedAt,
        canApprove: this.#o.link.canApprove,
      });
      this.#challenges.set(b.id, turnId);
      this.#changed();
      return;
    }
    const state: Partial<Record<Gate2Bubble['state'], ConsentState>> = {
      signing: 'sending', approved: 'sent', denied: 'cancelled', expired: 'expired', refused: 'refused',
    };
    const next = state[b.state];
    if (b.state === 'awaiting') {
      const t = this.conv.get(turnId);
      if (t?.kind === 'consent' && t.state === 'sending') this.conv.update(turnId, { state: 'awaiting' });
      if (b.note) trace('Gate 2 note', { note: b.note });
      return;
    }
    if (next) this.conv.update(turnId, { state: next });
  }

  #undeliverable(p: Packet): void {
    trace('a packet was not acknowledged', { kind: p.kind });
    if (p.kind === PacketKind.Gate2Response) {
      const turn = this.#challenges.get(p.packet.challenge_id);
      if (turn) this.conv.update(turn, { state: 'uncertain' });
    } else if (p.kind === PacketKind.Control && p.packet.op === ControlOp.AddGoal) {
      this.conv.add({ kind: 'say', text: this.#d.reply.undelivered, tone: 'problem' });
    } else if (p.kind === PacketKind.Dialogue && p.packet.dialogue_type === DialogueType.UserClarification) {
      const turn = this.#probes.get(p.packet.in_reply_to);
      if (turn) this.conv.update(turn, { state: 'waiting', answer: null });
    }
  }

  // -- time -------------------------------------------------------------------------------

  #expire(): void {
    this.#flow?.expire(this.#clock());
  }

  #settleUnresent(): void {
    if (!this.#online()) return;
    for (const t of this.conv.list()) {
      if (t.kind === 'ask' && t.state === 'waiting') this.conv.update(t.id, { state: 'expired' });
      if (t.kind === 'consent' && t.state === 'waiting') this.conv.update(t.id, { state: 'expired' });
    }
  }

  #online(): boolean {
    return this.#presence.phase === 'connected' || this.#presence.phase === 'demo';
  }

  #challengeOf(turnId: string): string | undefined {
    return [...this.#challenges].find(([, id]) => id === turnId)?.[0];
  }

  #openCards(): number {
    return this.conv.list().filter((t) => (t.kind === 'ask' && t.state === 'open') || (t.kind === 'consent' && t.state === 'awaiting')).length;
  }

  #changed(): void {
    for (const cb of this.#listeners) cb();
  }
}
