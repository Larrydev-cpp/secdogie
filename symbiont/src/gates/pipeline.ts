/**
 * The dual Socratic gates, end to end, inside the conversation.
 *
 *   intent ─▶ Gate 1 (the mind) ── question? ──▶ attention queue ──(gap)──▶ inline question
 *                │                                                            │ answer
 *                ◀────────────────────────────────────────────────────────────┘
 *                │ aligned (and only then)
 *                ▼
 *        WASM plan_action ── low risk ─────────────────────────────▶ released
 *                │ high risk
 *                ▼
 *        Gate 2 (the hand) ── challenge ──▶ attention queue ──(gap)──▶ inline signature bubble
 *                                                                          │ operator signs
 *        issuer verifies the token (action hash, subject, window, trust) ◀─┘ ──▶ released
 *
 * Every question and every signature is a proposal the attention queue holds
 * during flow and surfaces in a gap; nothing is modal, nothing is approved by
 * default, and silence until a deadline is a "no". The agent and the operator
 * are two different keys: the issuer holds the agent's, the signer flow asks
 * the operator's keyring, which in a browser opens only on a real click.
 *
 * Clocks: the attention layer counts milliseconds; the gates speak the
 * Python wire's seconds. One injected millisecond clock drives both.
 */

import type { AttentionScheduler } from '../attention/scheduler.ts';
import type { QueueEvents } from '../attention/queue.ts';
import type { TopologySnapshot } from '../gate1/interpret.ts';
import { targetLabel } from '../gate1/interpret.ts';
import { ProbeLedger } from '../gate1/ledger.ts';
import { type AlignedIntent, Gate1Machine, type Gate1Step, isAligned } from '../gate1/machine.ts';
import type { Gate2Issuer, PlanPreview, Release, Settlement } from '../gate2/issuer.ts';
import { type ApproveResult, Gate2SignerFlow, type OperatorKeyring } from '../gate2/signer_flow.ts';
import { DialogueType, type Gate2ChallengePacket, dialoguePacket, randomHex } from '../gate2/wire.ts';
import type { ConsciousnessStream } from '../stream/stream.ts';

export interface Planner {
  planAction(stateKey: string, verb: 'navigate' | 'submit'): PlanPreview;
}

export interface PipelineOptions {
  readonly stream: ConsciousnessStream;
  readonly scheduler: AttentionScheduler;
  readonly topology: () => TopologySnapshot;
  readonly planner: Planner;
  readonly issuer: Gate2Issuer;
  readonly keyring: OperatorKeyring;
  /** Milliseconds. */
  readonly clock: () => number;
  readonly probeTtlSeconds?: number;
  readonly armingDelaySeconds?: number;
  readonly onRelease?: (r: Release, intent: AlignedIntent) => void;
}

export function riskExplanation(p: PlanPreview): string {
  const where = `${p.origin.replace(/^https:\/\//, '')}${p.route}`;
  if (p.target_action.kind === 'navigate') return `打开 ${where}。`;
  const fields = p.fields.length ? `（字段：${p.fields.join('、')}）` : '';
  const tail = p.risk === 'irreversible' ? '这类操作在站点上通常无法撤回。' : '这会改变站点上的数据。';
  return `向 ${where} 提交 ${p.method.toUpperCase()} 表单${fields}。${tail}`;
}

export class DualGatePipeline {
  readonly #o: PipelineOptions;
  readonly #ledger: ProbeLedger;
  readonly #flow: Gate2SignerFlow;
  /** Gate 1 machines by id, and which machine owns each open probe. */
  readonly #machines = new Map<string, Gate1Machine>();
  readonly #probeOwner = new Map<string, string>();
  /** Gate 2 challenges by id, with the intent they serve. */
  readonly #challenges = new Map<string, { packet: Gate2ChallengePacket; intent: AlignedIntent }>();
  readonly releases: Release[] = [];
  /** Queue events are handled one after another, in order. */
  #work: Promise<void> = Promise.resolve();

  constructor(o: PipelineOptions) {
    this.#o = o;
    const sec = () => o.clock() / 1000;
    this.#ledger = new ProbeLedger({ clock: sec, ...(o.probeTtlSeconds ? { ttlSeconds: o.probeTtlSeconds } : {}) });
    this.#flow = new Gate2SignerFlow({
      peerDid: o.issuer.agent.did,
      keyring: o.keyring,
      clock: sec,
      ...(o.armingDelaySeconds !== undefined ? { armingDelaySeconds: o.armingDelaySeconds } : {}),
      // The in-page channel: the operator's response goes straight to the issuer.
      send: async (resp) => this.#settle(await o.issuer.receive(resp, sec())),
    });
    this.#flow.onChange = (b) => {
      const prev = o.stream.gate2(b.id);
      o.stream.putGate2({ id: b.id, at: prev?.at ?? o.clock(), veiled: b.veiled, bubble: b });
    };
    o.scheduler.onTick((ev) => {
      this.#work = this.#work.then(() => this.#onTick(ev));
    });
  }

  /** Resolves once every queue event so far has been reflected in the stream. */
  settled(): Promise<void> {
    return this.#work;
  }

  get #sec(): number {
    return this.#o.clock() / 1000;
  }

  // ---- Gate 1 -------------------------------------------------------------------

  /** A new intent from the person. Returns its id. */
  async submit(instruction: string): Promise<string> {
    const id = randomHex(6);
    const m = new Gate1Machine({ id, instruction, topology: this.#o.topology, ledger: this.#ledger });
    this.#machines.set(id, m);
    await this.#gate1(m, m.start());
    return id;
  }

  async #gate1(m: Gate1Machine, step: Gate1Step): Promise<void> {
    const { stream, scheduler } = this.#o;
    switch (step.kind) {
      case 'probe':
        this.#probeOwner.set(step.probe.probeId, m.id);
        scheduler.queue.enqueue(
          {
            id: step.probe.probeId,
            kind: 'gate1',
            payload: { intent: m.id, finding: step.finding.code },
            summary: step.probe.question,
            expiresAt: step.probe.expiresAt * 1000,
          },
          this.#o.clock(),
        );
        this.#held();
        scheduler.review();
        return;
      case 'aligned':
        stream.narrate({ type: 'aligned', summary: step.intent.instruction, at: this.#o.clock() });
        await this.#plan(step.intent);
        return;
      case 'declined':
        stream.narrate({
          type: 'declined',
          summary: [...step.reasons, ...step.alternatives.map((a) => `可以改为：${a}`)].join('；'),
          at: this.#o.clock(),
        });
        return;
      case 'expired':
        stream.narrate({ type: 'expired', summary: m.instruction, at: this.#o.clock() });
        return;
      case 'ignored':
        return;
    }
  }

  /** The person's answer to a surfaced Gate 1 question. */
  async answer(probeId: string, text: string): Promise<void> {
    const owner = this.#probeOwner.get(probeId);
    const m = owner === undefined ? undefined : this.#machines.get(owner);
    const item = this.#o.scheduler.queue.get(probeId);
    if (!m || item?.state !== 'surfaced' || item.veiled) return;
    const pkt = dialoguePacket({
      probe_id: randomHex(8),
      dialogue_type: DialogueType.UserClarification,
      content: text,
      in_reply_to: probeId,
    });
    const step = m.answer(pkt, this.#o.keyring.did, this.#sec);
    if (step.kind === 'ignored') return;
    this.#o.scheduler.queue.resolve(probeId, 'answered');
    const prev = this.#o.stream.gate1(probeId);
    if (prev) this.#o.stream.putGate1({ ...prev, state: 'answered', answer: text });
    this.#probeOwner.delete(probeId);
    await this.#gate1(m, step);
  }

  // ---- WASM plan -> Gate 2 ---------------------------------------------------------

  async #plan(intent: AlignedIntent): Promise<void> {
    // The type says "AlignedIntent"; this says "and Gate 1 issued it".
    if (!isAligned(intent)) throw new Error('refusing to plan an intent Gate 1 did not align');
    const preview = this.#o.planner.planAction(intent.target.stateKey, intent.verb);
    const r = await this.#o.issuer.gate(preview, riskExplanation(preview));
    if (r.kind === 'released') {
      this.#release(r.release, intent);
      return;
    }
    this.#challenges.set(r.packet.challenge_id, { packet: r.packet, intent });
    this.#o.scheduler.queue.enqueue(
      {
        id: r.packet.challenge_id,
        kind: 'gate2',
        payload: r.packet,
        summary: riskExplanation(preview),
        expiresAt: r.packet.expires_at * 1000,
      },
      this.#o.clock(),
    );
    this.#held();
    this.#o.scheduler.review();
  }

  async approve(challengeId: string): Promise<ApproveResult> {
    const res = await this.#flow.approve(challengeId, this.#sec);
    if (res.ok) this.#o.scheduler.queue.resolve(challengeId, 'approved');
    return res;
  }

  async deny(challengeId: string): Promise<ApproveResult> {
    const res = await this.#flow.deny(challengeId, this.#sec);
    if (res.ok && this.#o.scheduler.queue.get(challengeId)?.state === 'surfaced') {
      this.#o.scheduler.queue.resolve(challengeId, 'denied');
    }
    return res;
  }

  #settle(s: Settlement): void {
    const c = this.#challenges.get(s.kind === 'released' ? s.release.challengeId ?? '' : s.challengeId);
    if (!c) return;
    this.#challenges.delete(c.packet.challenge_id);
    if (s.kind === 'released') this.#release(s.release, c.intent);
    else {
      this.#o.stream.narrate({
        type: 'refused',
        target: targetLabel(c.intent.target),
        reason: s.reason,
        at: this.#o.clock(),
      });
    }
  }

  #release(r: Release, intent: AlignedIntent): void {
    this.releases.push(r);
    this.#o.stream.narrate({ type: 'released', target: targetLabel(intent.target), at: this.#o.clock() });
    this.#o.onRelease?.(r, intent);
  }

  // ---- the attention queue ---------------------------------------------------------

  async #onTick(ev: QueueEvents): Promise<void> {
    const { stream } = this.#o;
    const now = this.#o.clock();
    for (const p of ev.surfaced) {
      if (p.kind === 'gate1') {
        const probe = this.#ledger.get(p.id);
        if (!probe) continue;
        stream.putGate1({
          id: p.id,
          at: now,
          veiled: false,
          state: 'open',
          question: probe.question,
          finding: probe.gateFinding,
          options: probe.options,
          answer: null,
        });
      } else {
        await this.#flow.present(p.payload as Gate2ChallengePacket, now / 1000);
      }
    }
    for (const p of ev.expired) {
      if (p.kind === 'gate1') {
        const m = this.#machines.get(this.#probeOwner.get(p.id) ?? '');
        const prev = stream.gate1(p.id);
        if (prev) stream.putGate1({ ...prev, state: 'expired', veiled: false });
        if (m) await this.#gate1(m, m.expire(now / 1000) ?? m.withdraw('the question expired'));
      } else {
        this.#flow.expire(now / 1000);
        for (const id of this.#o.issuer.expire(now / 1000)) {
          this.#settle({ kind: 'refused', challengeId: id, reason: '没等到签名' });
        }
      }
    }
    for (const p of ev.veiled) this.#veil(p.id, p.kind, true);
    for (const p of ev.unveiled) this.#veil(p.id, p.kind, false);
    this.#held();
  }

  #veil(id: string, kind: 'gate1' | 'gate2', on: boolean): void {
    if (kind === 'gate2') {
      if (on) this.#flow.veil(id);
      else this.#flow.unveil(id, this.#sec);
      return;
    }
    const prev = this.#o.stream.gate1(id);
    if (prev) this.#o.stream.putGate1({ ...prev, veiled: on });
  }

  #held(): void {
    this.#o.stream.setHeld(this.#o.scheduler.queue.queued.length);
  }

  /** For rendering: the epoch seconds the gates run on. */
  nowSeconds(): number {
    return this.#sec;
  }
}
