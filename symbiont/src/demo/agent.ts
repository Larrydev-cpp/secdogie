/**
 * The demo's node: a small scripted stand-in for `secdogie-node`, living in the
 * page and speaking the same signed packets over the same frames and session
 * -- so the demo exercises the page's real client, not a mock of it. It
 * touches nothing: no desktop, no network, no storage.
 *
 * What it does with a goal:
 *  - a deleting goal ("注销旧账号", "close the old account") finds two similar
 *    places and asks which one (Gate 1), then asks for the operator's signature
 *    on the irreversible step (Gate 2) and verifies the token the way a node's
 *    gate does before "doing" anything;
 *  - a goal whose wording the Socratic rules question (gate1/rules.ts) gets that
 *    question first;
 *  - anything else is simply "done" after a moment.
 */

import type { Signer } from '../core/ed25519.ts';
import { TrustSet } from '../core/envelope.ts';
import { mentionsDestruction, reviewWording } from '../gate1/rules.ts';
import { type TargetAction, actionHash, verifyAuthorization } from '../gate2/authz.ts';
import {
  ControlOp,
  DialogueType,
  type Gate2ChallengePacket,
  type Packet,
  PacketKind,
  RiskLevel,
  SessionEvent,
  Verdict,
  dialoguePacket,
  randomHex,
} from '../gate2/wire.ts';
import { DirectFrames } from '../net/frame.ts';
import { DIALOGUE_CHANNEL, muxDecode, muxEncode } from '../net/mux.ts';
import { DialogueSession } from '../net/session.ts';

export interface AgentOptions {
  readonly node: Signer;
  readonly appDid: string;
  readonly operatorDid: string;
  readonly lang: 'zh' | 'en';
  /** Frames to the page. */
  readonly send: (raw: Uint8Array<ArrayBuffer>) => void;
  readonly clock?: () => number;
  readonly delay?: (ms: number) => Promise<void>;
}

const CLOSE_ACCOUNT = /注销|close (the |my )?(old )?account|delete (the |my )?(old )?account/i;

const WORDS = {
  zh: {
    places: ['docs.example.com 的「账号设置 › 注销账号」', 'forum.example.com 的「设置 › 注销账号」'],
    name: '注销旧账号',
    fine: ['好，就这么改', '按原来的来'],
  },
  en: {
    places: ['docs.example.com — Account settings › Close account', 'forum.example.com — Settings › Close account'],
    name: 'Close the old account',
    fine: ['Fine, change it', 'Keep it as I said'],
  },
} as const;

interface Goal {
  readonly id: string;
  readonly title: string;
  step: 'start' | 'asked-place' | 'asked-wording' | 'awaiting-signature' | 'done';
  probe: string | null;
  challenge: Gate2ChallengePacket | null;
}

export class DemoAgent {
  readonly #o: AgentOptions;
  readonly #frames: DirectFrames;
  readonly #session: DialogueSession;
  readonly #clock: () => number;
  readonly #delay: (ms: number) => Promise<void>;
  #goal: Goal | null = null;
  #question: Packet | null = null;

  constructor(o: AgentOptions) {
    this.#o = o;
    this.#clock = o.clock ?? (() => Date.now() / 1000);
    this.#delay = o.delay ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
    const trust = new TrustSet([o.appDid]);
    this.#frames = new DirectFrames({ self: o.node, peerDid: o.appDid, trust });
    this.#session = new DialogueSession({
      self: o.node,
      peerDid: o.appDid,
      trust,
      send: (f) => {
        void this.#frames.build(muxEncode(DIALOGUE_CHANNEL, f)).then((raw) => o.send(raw));
      },
    });
    this.#session.onEnvelope = ({ packet }) => void this.#on(packet);
  }

  /** One frame from the page. */
  async receive(raw: Uint8Array): Promise<void> {
    const data = await this.#frames.open(raw);
    if (data === null) return;
    const m = muxDecode(data);
    if (m?.channel === DIALOGUE_CHANNEL) await this.#session.receive(m.payload);
  }

  start(): void {
    this.#session.start(100);
  }

  stop(): void {
    this.#session.stop();
  }

  async #status(content: string, about = ''): Promise<void> {
    await this.#session.send({
      kind: PacketKind.Dialogue,
      packet: dialoguePacket({ probe_id: `st-${randomHex(4)}`, dialogue_type: DialogueType.SystemStatus, content, in_reply_to: about }),
    });
  }

  async #current(): Promise<void> {
    const g = this.#goal;
    if (!g || g.step === 'done') return this.#status('status: idle');
    const waiting = g.step === 'asked-place' || g.step === 'asked-wording' || g.step === 'awaiting-signature';
    return this.#status(waiting ? 'status: waiting for operator' : 'status: running', g.id);
  }

  async #ask(options: readonly string[], finding: string): Promise<string> {
    const probe = `p-${randomHex(6)}`;
    this.#question = {
      kind: PacketKind.Dialogue,
      packet: dialoguePacket({ probe_id: probe, dialogue_type: DialogueType.SocraticQuestion, content: '', suggested_options: [...options], gate_finding: finding }),
    };
    await this.#session.send(this.#question);
    return probe;
  }

  async #on(p: Packet): Promise<void> {
    if (p.kind === PacketKind.Session && p.packet.event === SessionEvent.Hello) {
      await this.#current();
      if (this.#question && this.#goal && this.#goal.step !== 'done' && this.#goal.step !== 'awaiting-signature') await this.#session.send(this.#question);
      if (this.#goal?.challenge && this.#goal.step === 'awaiting-signature') {
        await this.#session.send({ kind: PacketKind.Gate2Challenge, packet: this.#goal.challenge });
      }
      return;
    }
    if (p.kind === PacketKind.Control) {
      const c = p.packet;
      if (c.op === ControlOp.AddGoal) {
        if (this.#goal && this.#goal.step !== 'done') {
          await this.#status(`refused: goal ${this.#goal.id} is still running`, c.request_id);
          return;
        }
        this.#goal = { id: c.goal_id, title: c.title, step: 'start', probe: null, challenge: null };
        await this.#status(`accepted: goal ${c.goal_id} queued`, c.request_id);
        await this.#delay(400);
        await this.#current();
        await this.#run();
      } else if (c.op === ControlOp.Stop && this.#goal && c.goal_id === this.#goal.id) {
        await this.#status(`accepted: stop requested for ${c.goal_id}`, c.request_id);
        await this.#finish(false, 'stopped by the operator');
      } else {
        await this.#status(`refused: ${c.op} is not supported in the demo`, c.request_id);
      }
      return;
    }
    const g = this.#goal;
    if (!g) return;
    if (p.kind === PacketKind.Dialogue && p.packet.dialogue_type === DialogueType.UserClarification && p.packet.in_reply_to === g.probe) {
      await this.#status('answer adopted', g.probe);
      this.#question = null;
      if (g.step === 'asked-place') {
        const forum = p.packet.content === WORDS[this.#o.lang].places[1];
        await this.#delay(500);
        await this.#challenge(forum ? 'https://forum.example.com/account/delete' : 'https://docs.example.com/account/delete');
      } else if (g.step === 'asked-wording') {
        await this.#delay(700);
        await this.#finish(true, 'done');
      }
      return;
    }
    if (p.kind === PacketKind.Gate2Response && g.challenge && p.packet.challenge_id === g.challenge.challenge_id) {
      const r = p.packet;
      if (r.user_verdict !== Verdict.Approve) {
        await this.#finish(false, 'the operator declined');
        return;
      }
      // what a node's gate does: the token must verify for exactly this action, this node, now
      const ok = await verifyAuthorization(r.authorization, g.challenge.target_action, {
        operators: new TrustSet([this.#o.operatorDid]),
        subject: this.#o.node.did,
        now: this.#clock(),
      });
      g.step = 'start';
      await this.#current();
      await this.#delay(900);
      await this.#finish(ok.ok, ok.ok ? 'account closed (demo: nothing was touched)' : `authorization refused: ${ok.reason}`);
    }
  }

  async #run(): Promise<void> {
    const g = this.#goal!;
    const w = WORDS[this.#o.lang];
    if (CLOSE_ACCOUNT.test(g.title) || mentionsDestruction(g.title)) {
      g.step = 'asked-place';
      g.probe = await this.#ask(w.places, 'target-ambiguous');
      return;
    }
    const review = reviewWording(g.title);
    if (review.verdict === 'revise' && review.findings[0]) {
      g.step = 'asked-wording';
      g.probe = await this.#ask(w.fine, review.findings[0].code);
      return;
    }
    await this.#delay(1200);
    await this.#finish(true, 'done');
  }

  async #challenge(target: string): Promise<void> {
    const g = this.#goal!;
    const action: TargetAction = {
      kind: 'submit', target_id: target, target_role: 'form', target_name: WORDS[this.#o.lang].name, text: '', high_risk: true,
    };
    g.challenge = {
      challenge_id: randomHex(8),
      target_action: action,
      risk_level: RiskLevel.Irreversible,
      risk_explanation: 'submit on Close account; rollback: none: declared irreversible',
      action_hash: await actionHash(action),
      subject_did: this.#o.node.did,
      expires_at: this.#clock() + 120,
    };
    g.step = 'awaiting-signature';
    await this.#current();
    await this.#session.send({ kind: PacketKind.Gate2Challenge, packet: g.challenge });
  }

  async #finish(ok: boolean, summary: string): Promise<void> {
    const g = this.#goal;
    if (!g || g.step === 'done') return;
    g.step = 'done';
    this.#question = null;
    await this.#status(`goal ${g.id} finished: exit ${ok ? 0 : 1} -- ${summary}`);
    await this.#current();
  }
}
