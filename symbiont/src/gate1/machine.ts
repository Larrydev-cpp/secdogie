/**
 * Gate 1 -- Socratic semantic alignment, as a state machine.
 *
 *   received ──review──▶ probing ──answer──▶ (review again) ──▶ aligned
 *                           │                                    declined
 *                           └──deadline──▶ expired
 *
 * Every round reviews the intent as it now stands: the wording rules
 * (`socratic.py`), the graph rules (is the target real, is it unique, does it
 * ask to remove mapped state) and the intent contract (`action_gate.py`). The
 * first finding becomes one Socratic question -- one at a time -- with the
 * alternatives as suggested options. The human's clarification is folded into
 * the intent and the review runs again.
 *
 * There is no implicit yes. An unanswered question expires and the intent with
 * it; after `maxRounds` questions without alignment the machine declines and
 * says what it would do instead. Only `aligned` yields an {@link AlignedIntent},
 * and only an AlignedIntent minted here passes {@link isAligned} -- the
 * pipeline will not ask the WASM engine for a plan without one.
 */

import type { DialoguePacket } from '../gate2/wire.ts';
import { type IntentContract, checkContract, emptyContract } from './intent.ts';
import {
  type IntentInterpreter,
  LexicalInterpreter,
  type TopologySnapshot,
  type TopologyTarget,
  type Verb,
  targetLabel,
} from './interpret.ts';
import { type Probe, type ProbeLedger, type Resolution } from './ledger.ts';
import { type Finding, type FindingCode, checkGraphDestructive, mentionsDestruction, reviewWording } from './rules.ts';

export type Gate1State = 'received' | 'probing' | 'aligned' | 'declined' | 'expired';

export interface AlignedIntent {
  readonly id: string;
  readonly instruction: string;
  readonly verb: Verb;
  readonly target: TopologyTarget;
  readonly contract: IntentContract;
  readonly mutating: boolean;
  readonly destructive: boolean;
  readonly clarifications: readonly Resolution[];
}

const ISSUED = new WeakSet<object>();

/** True only for an AlignedIntent this module issued. */
export function isAligned(x: unknown): x is AlignedIntent {
  return typeof x === 'object' && x !== null && ISSUED.has(x);
}

export type Gate1Step =
  | { readonly kind: 'probe'; readonly probe: Probe; readonly packet: DialoguePacket; readonly finding: Finding }
  | { readonly kind: 'aligned'; readonly intent: AlignedIntent }
  | { readonly kind: 'declined'; readonly reasons: readonly string[]; readonly alternatives: readonly string[] }
  | { readonly kind: 'expired'; readonly probeId: string }
  | { readonly kind: 'ignored'; readonly reason: string };

type Effect =
  | { readonly type: 'pin'; readonly target: TopologyTarget }
  | { readonly type: 'ack'; readonly code: FindingCode }
  | { readonly type: 'irreversible' }
  | { readonly type: 'reversible' }
  | { readonly type: 'read-only' }
  | { readonly type: 'decline'; readonly reason: string; readonly alternatives: readonly string[] };

interface Question {
  readonly text: string;
  readonly options: ReadonlyArray<readonly [string, Effect]>;
}

export const OPT = {
  stop: '先不做',
  readOnly: '只读看看',
  confirmChange: '确认要修改',
  askEachTime: '每次发出前先给我看',
  draftOnly: '只起草，不发出',
  eventDriven: '改成有变化时再通知',
  split: '拆成几个子目标',
  irreversible: '不可逆，我确认',
  narrowScope: '以后不再扫描这些地方',
} as const;

const STOP: Effect = { type: 'decline', reason: '你决定先不做', alternatives: [] };

export class Gate1Machine {
  readonly id: string;
  readonly #topology: () => TopologySnapshot;
  readonly #interpreter: IntentInterpreter;
  readonly #ledger: ProbeLedger;
  readonly #maxRounds: number;
  #state: Gate1State = 'received';
  #instruction: string;
  #context: string[] = [];
  #verb: Verb | null = null;
  #target: TopologyTarget | null = null;
  #contract: IntentContract = emptyContract;
  #acked = new Set<FindingCode>();
  #rounds = 0;
  #open: { probe: Probe; effects: Map<string, Effect>; finding: Finding } | null = null;
  #clarifications: Resolution[] = [];
  #outcome: Gate1Step | null = null;

  constructor(opts: {
    id: string;
    instruction: string;
    topology: () => TopologySnapshot;
    ledger: ProbeLedger;
    interpreter?: IntentInterpreter;
    maxRounds?: number;
  }) {
    this.id = opts.id;
    this.#instruction = opts.instruction;
    this.#topology = opts.topology;
    this.#ledger = opts.ledger;
    this.#interpreter = opts.interpreter ?? new LexicalInterpreter();
    this.#maxRounds = opts.maxRounds ?? 3;
  }

  get state(): Gate1State {
    return this.#state;
  }

  get instruction(): string {
    return this.#instruction;
  }

  get openProbeId(): string | null {
    return this.#open?.probe.probeId ?? null;
  }

  /** The step that ended this machine, once it has ended. */
  get outcome(): Gate1Step | null {
    return this.#outcome;
  }

  start(): Gate1Step {
    if (this.#state !== 'received') throw new Error(`cannot start from ${this.#state}`);
    return this.#review();
  }

  /** Applies the human's answer to the open question, then reviews again. */
  answer(pkt: DialoguePacket, answeredBy: string, now?: number): Gate1Step {
    const open = this.#open;
    if (this.#state !== 'probing' || open === null || pkt.in_reply_to !== open.probe.probeId) {
      return { kind: 'ignored', reason: 'no open question of this intent has that id' };
    }
    const res = this.#ledger.resolve(pkt, answeredBy, now);
    if (res === null) {
      if (this.#ledger.status(open.probe.probeId) === 'expired') return this.#expire(open.probe.probeId);
      return { kind: 'ignored', reason: 'not an answer to an open question' };
    }
    this.#open = null;
    this.#clarifications.push(res);
    const effect = open.effects.get(res.answer.trim());
    if (effect) {
      const end = this.#apply(effect, open.finding.code);
      if (end) return end;
    } else {
      this.#freeText(res.answer.trim(), open.finding.code);
    }
    return this.#review();
  }

  /** Expires the open question if its deadline passed. */
  expire(now?: number): Gate1Step | null {
    const open = this.#open;
    if (this.#state !== 'probing' || open === null) return null;
    if (!this.#ledger.expire(now).includes(open.probe.probeId) && this.#ledger.status(open.probe.probeId) !== 'expired') {
      return null;
    }
    return this.#expire(open.probe.probeId);
  }

  /** The intent was withdrawn (by the human, or the surface went away). */
  withdraw(reason: string): Gate1Step {
    if (this.#open) this.#ledger.withdraw(this.#open.probe.probeId);
    this.#open = null;
    return this.#end({ kind: 'declined', reasons: [reason], alternatives: [] });
  }

  #expire(probeId: string): Gate1Step {
    this.#open = null;
    this.#state = 'expired';
    this.#outcome = { kind: 'expired', probeId };
    return this.#outcome;
  }

  #end(step: Gate1Step): Gate1Step {
    this.#state = step.kind === 'aligned' ? 'aligned' : step.kind === 'expired' ? 'expired' : 'declined';
    this.#outcome = step;
    return step;
  }

  #apply(e: Effect, code: FindingCode): Gate1Step | null {
    switch (e.type) {
      case 'pin':
        this.#target = e.target;
        return null;
      case 'ack':
        this.#acked.add(e.code);
        return null;
      case 'irreversible':
        this.#contract = { ...this.#contract, irreversible: true, rollback: '' };
        return null;
      case 'reversible':
        this.#contract = { ...this.#contract, irreversible: false };
        this.#acked.add(code);
        return null;
      case 'read-only':
        this.#verb = 'navigate';
        this.#acked.add(code);
        return null;
      case 'decline':
        return this.#end({ kind: 'declined', reasons: [e.reason], alternatives: e.alternatives });
    }
  }

  /** An answer in the human's own words. */
  #freeText(text: string, code: FindingCode): void {
    switch (code) {
      case 'intent-unproven':
      case 'intent-contradiction':
        this.#contract = { ...this.#contract, rollback: text, irreversible: false };
        break;
      case 'target-unproven':
      case 'target-ambiguous':
        this.#context.push(text);
        this.#target = null;
        break;
      default:
        // The human restated the request: review the new words.
        this.#instruction = text;
        this.#acked.delete(code);
    }
  }

  #findings(): { finding: Finding; question: Question } | { aligned: AlignedIntent } {
    const instruction = this.#instruction;
    const wording = reviewWording(instruction).findings.find((f) => !this.#acked.has(f.code));
    if (wording) return { finding: wording, question: this.#wordingQuestion(wording) };

    const destructiveGraph = checkGraphDestructive(instruction);
    if (destructiveGraph && !this.#acked.has('graph-destructive')) {
      return {
        finding: destructiveGraph,
        question: {
          text: '已经梳理出的拓扑不会被删除——v1 的状态图只追加。你是想以后不再扫描这些地方，还是只想看看已有的路由？',
          options: [
            [OPT.narrowScope, { type: 'decline', reason: 'v1 的状态图不删除已映射的状态', alternatives: ['在沙箱面板里收窄抓取白名单，以后不再扫描那些 origin'] }],
            [OPT.readOnly, { type: 'read-only' }],
            [OPT.stop, STOP],
          ],
        },
      };
    }

    const topo = this.#topology();
    const words = [instruction, ...this.#context].join(' ');
    const interp = this.#interpreter.interpret(words, topo, this.#verb ? { verb: this.#verb } : {});
    const verb = this.#verb ?? interp.verb;
    // A pinned target stays pinned only while the graph still holds it.
    if (this.#target && !topo.targets.some((t) => t.stateKey === this.#target!.stateKey)) this.#target = null;
    const target = this.#target ?? (interp.matches.length === 1 ? interp.matches[0]! : null);

    if (target === null && interp.matches.length === 0) {
      const known = (verb === 'submit' ? topo.targets.filter((t) => t.form) : topo.targets).slice(0, 4);
      return {
        finding: {
          code: 'target-unproven',
          reason: 'the target is not in the mapped topology; Gate 1 does not guess one',
          suggestion: 'Point at a mapped state, or map the site first.',
        },
        question: {
          text: known.length
            ? '我在已梳理的拓扑里找不到你说的目标。你指的是下面哪一个？也可以换个说法。'
            : '拓扑里还没有任何可以操作的目标。先把相关站点梳理一遍，再回来做这件事？',
          options: [...known.map((t) => [targetLabel(t), { type: 'pin', target: t }] as const), [OPT.stop, STOP]],
        },
      };
    }
    if (target === null) {
      return {
        finding: {
          code: 'target-ambiguous',
          reason: `${interp.matches.length} mapped states fit the request equally`,
          suggestion: 'Say which one.',
        },
        question: {
          text: `有 ${interp.matches.length} 个地方都对得上。你指的是哪一个？`,
          options: [...interp.matches.map((t) => [targetLabel(t), { type: 'pin', target: t }] as const), [OPT.stop, STOP]],
        },
      };
    }

    const mutating = verb === 'submit' && target.form?.method === 'post';
    const destructive =
      mutating && (mentionsDestruction(target.route) || (target.form?.fields ?? []).some(mentionsDestruction));
    const contract: IntentContract = { ...this.#contract, purpose: this.#contract.purpose || instruction };
    const gap = checkContract(contract, { mutating, destructive });
    if (gap && !this.#acked.has(gap.code)) {
      return {
        finding: gap,
        question:
          gap.code === 'intent-contradiction'
            ? {
                text: '你既说这一步不可逆，又给了回退的办法。到底能不能撤回？',
                options: [
                  [OPT.irreversible, { type: 'irreversible' }],
                  [OPT.stop, STOP],
                ],
              }
            : {
                text: `${targetLabel(target)} 这一步看起来做了就收不回来。有办法撤回吗？如果没有，你确认要这样做吗？`,
                options: [
                  [OPT.irreversible, { type: 'irreversible' }],
                  [OPT.stop, STOP],
                ],
              },
      };
    }

    const aligned: AlignedIntent = {
      id: this.id,
      instruction,
      verb,
      target,
      contract,
      mutating,
      destructive,
      clarifications: [...this.#clarifications],
    };
    Object.freeze(aligned);
    ISSUED.add(aligned);
    return { aligned };
  }

  #wordingQuestion(f: Finding): Question {
    switch (f.code) {
      case 'contradiction':
        return {
          text: '你说只读，可也提到了修改。这一次是只看，还是确实要改？',
          options: [
            [OPT.readOnly, { type: 'read-only' }],
            [OPT.confirmChange, { type: 'ack', code: 'contradiction' }],
            [OPT.stop, STOP],
          ],
        };
      case 'unattended-posting':
        return {
          text: '替你发出去的内容，要不要每次先给你过目？不经人看就发，我不会这么做。',
          options: [
            [OPT.askEachTime, { type: 'ack', code: 'unattended-posting' }],
            [OPT.draftOnly, { type: 'read-only' }],
            [OPT.stop, STOP],
          ],
        };
      case 'polling':
        return {
          text: '这么频繁地反复检查很浪费。改成有变化时再通知你，好吗？',
          options: [
            [OPT.eventDriven, { type: 'ack', code: 'polling' }],
            [OPT.stop, STOP],
          ],
        };
      case 'overlong':
        return {
          text: '这件事的步骤太多，一次说不清。先拆成几个子目标，一个一个来？',
          options: [
            [OPT.split, { type: 'decline', reason: '一次的步骤太多', alternatives: ['拆成几个子目标，逐个发起'] }],
            [OPT.stop, STOP],
          ],
        };
      default:
        return { text: '你想让我做成什么？说一个具体的目标就好。', options: [] };
    }
  }

  #review(): Gate1Step {
    const r = this.#findings();
    if ('aligned' in r) return this.#end({ kind: 'aligned', intent: r.aligned });
    if (this.#rounds >= this.#maxRounds) {
      return this.#end({
        kind: 'declined',
        reasons: [`问了 ${this.#rounds} 轮仍没有对齐：${r.finding.reason}`],
        alternatives: r.finding.suggestion ? [r.finding.suggestion] : [],
      });
    }
    this.#rounds++;
    const { probe, packet } = this.#ledger.open(r.question.text, {
      options: r.question.options.map(([label]) => label),
      gateFinding: r.finding.code,
    });
    this.#open = { probe, effects: new Map(r.question.options.map(([l, e]) => [l, e])), finding: r.finding };
    this.#state = 'probing';
    return { kind: 'probe', probe, packet, finding: r.finding };
  }
}
