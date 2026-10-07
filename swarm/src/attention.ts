import type {
  AttentionPhase,
  FocusSignal,
  Proposal,
  ProposalEvent,
} from "./protocol.js";

export interface AttentionPolicy {
  readonly flowFocusedMs: number;
  readonly idleAfterMs: number;
  readonly minGapMs: number;
  readonly cooldownMs: number;
  readonly resonanceThreshold: number;
  readonly maxQueue: number;
}

const DEFAULT_POLICY: AttentionPolicy = {
  flowFocusedMs: 10_000,
  idleAfterMs: 45_000,
  minGapMs: 2_500,
  cooldownMs: 20_000,
  resonanceThreshold: 0.66,
  maxQueue: 32,
};

export function evaluateAttention(signal: FocusSignal, policy: AttentionPolicy = DEFAULT_POLICY): AttentionPhase {
  if (signal.sensitiveContext) return "sensitive";
  if (signal.idleForMs >= policy.idleAfterMs) return "idle";
  if (signal.phaseHint === "flow" || signal.focusedForMs >= policy.flowFocusedMs) return "flow";
  return "transition";
}

function overlapScore(a: readonly string[], b: readonly string[]): number {
  if (!a.length || !b.length) return 0;
  const other = new Set(b);
  const overlap = a.filter((tag) => other.has(tag)).length;
  return overlap / Math.max(a.length, b.length);
}

export class ProposalQueue {
  private readonly queue: Proposal[] = [];
  private lastReleasedAtMs = Number.NEGATIVE_INFINITY;

  constructor(private readonly policy: AttentionPolicy = DEFAULT_POLICY) {}

  enqueue(proposal: Proposal): ProposalEvent {
    if (this.queue.length >= this.policy.maxQueue) this.queue.shift();
    this.queue.push(proposal);
    return { proposal, disposition: "queued", attention: "transition", reason: "mutation queued" };
  }

  inspect(signal: FocusSignal): ProposalEvent | null {
    const phase = evaluateAttention(signal, this.policy);
    const candidate = this.queue[0];
    if (!candidate) return null;
    if (phase === "sensitive") {
      return { proposal: candidate, disposition: "suppressed-sensitive", attention: phase, reason: "sensitive context" };
    }
    const resonance = overlapScore(candidate.contextTags, signal.contextTags);
    const gapSatisfied = signal.observedAtMs - this.lastReleasedAtMs >= this.policy.minGapMs;
    const cooldownSatisfied = signal.observedAtMs - this.lastReleasedAtMs >= this.policy.cooldownMs;
    const organicWindow = phase === "idle" || (phase === "transition" && resonance >= this.policy.resonanceThreshold);
    if (phase === "flow") {
      return { proposal: candidate, disposition: "deferred-flow", attention: phase, reason: "flow state" };
    }
    if (!organicWindow || !gapSatisfied || (!cooldownSatisfied && candidate.highRisk)) {
      return { proposal: candidate, disposition: "queued", attention: phase, reason: "waiting for a safer attention gap" };
    }
    this.queue.shift();
    this.lastReleasedAtMs = signal.observedAtMs;
    return { proposal: candidate, disposition: "ready", attention: phase, reason: "attention gap + contextual resonance" };
  }

  expire(beforeMs: number): Proposal[] {
    const expired = this.queue.filter((proposal) => proposal.createdAtMs < beforeMs);
    for (const proposal of expired) this.queue.splice(this.queue.indexOf(proposal), 1);
    return expired;
  }

  get size(): number {
    return this.queue.length;
  }
}

export interface AttentionSink {
  onProposal(event: ProposalEvent): void;
}

export class AttentionEngine {
  private readonly queue: ProposalQueue;
  private timer: ReturnType<typeof setInterval> | null = null;
  private latestSignal: FocusSignal | null = null;

  constructor(
    private readonly sink: AttentionSink,
    private readonly policy: AttentionPolicy = DEFAULT_POLICY,
  ) {
    this.queue = new ProposalQueue(policy);
  }

  ingestFocus(signal: FocusSignal): void {
    this.latestSignal = signal;
    const event = this.queue.inspect(signal);
    if (event?.disposition === "ready" || event?.disposition === "suppressed-sensitive") {
      this.sink.onProposal(event);
    }
  }

  propose(proposal: Proposal): void {
    const event = this.queue.enqueue(proposal);
    this.sink.onProposal(event);
  }

  start(intervalMs = 500): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      if (!this.latestSignal) return;
      this.ingestFocus({ ...this.latestSignal, observedAtMs: Date.now() });
    }, intervalMs);
  }

  stop(): void {
    if (!this.timer) return;
    clearInterval(this.timer);
    this.timer = null;
  }

  close(): void {
    this.stop();
  }
}
