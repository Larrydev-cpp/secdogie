/**
 * Node liveness/health, mirroring `secdogie_swarm.registry`: EWMA latency and
 * error rate, heartbeats, and the healthy -> suspect -> dead classification.
 * "suspect" is the predictive signal so work moves off a degrading node before
 * its deadline. Time is passed in (seconds); nothing reads the clock.
 */

export const HEALTHY = 'healthy';
export const SUSPECT = 'suspect';
export const DEAD = 'dead';

export interface HealthPolicy {
  readonly alpha: number;
  readonly suspectAfterS: number;
  readonly deadAfterS: number;
  readonly latencySuspectS: number;
  readonly errorRateSuspect: number;
  readonly deadConsecutiveFailures: number;
}

export const DEFAULT_POLICY: HealthPolicy = {
  alpha: 0.3,
  suspectAfterS: 15,
  deadAfterS: 45,
  latencySuspectS: 4,
  errorRateSuspect: 0.4,
  deadConsecutiveFailures: 3,
};

export interface NodeHealth {
  did: string;
  lastSeen: number;
  latencyEwma: number;
  errorEwma: number;
  consecutiveFailures: number;
  status: string;
}

function recompute(n: NodeHealth, now: number, p: HealthPolicy): void {
  const idle = now - n.lastSeen;
  if (idle > p.deadAfterS || n.consecutiveFailures >= p.deadConsecutiveFailures) n.status = DEAD;
  else if (idle > p.suspectAfterS || n.latencyEwma > p.latencySuspectS || n.errorEwma > p.errorRateSuspect) n.status = SUSPECT;
  else n.status = HEALTHY;
}

export class NodeRegistry {
  readonly #nodes = new Map<string, NodeHealth>();
  readonly #p: HealthPolicy;

  constructor(policy: HealthPolicy = DEFAULT_POLICY) {
    this.#p = policy;
  }

  has(did: string): boolean {
    return this.#nodes.has(did);
  }

  get(did: string): NodeHealth | undefined {
    return this.#nodes.get(did);
  }

  heartbeat(did: string, now: number, latencyS?: number): NodeHealth {
    let n = this.#nodes.get(did);
    const a = this.#p.alpha;
    if (n === undefined) {
      n = { did, lastSeen: now, latencyEwma: latencyS ?? 0, errorEwma: 0, consecutiveFailures: 0, status: HEALTHY };
      this.#nodes.set(did, n);
    } else {
      n.lastSeen = now;
      if (latencyS !== undefined) n.latencyEwma = a * latencyS + (1 - a) * n.latencyEwma;
    }
    recompute(n, now, this.#p);
    return n;
  }

  record(did: string, ok: boolean, now: number, latencyS?: number): void {
    const n = this.#nodes.get(did);
    if (n === undefined) return;
    const a = this.#p.alpha;
    n.errorEwma = a * (ok ? 0 : 1) + (1 - a) * n.errorEwma;
    if (ok) {
      n.consecutiveFailures = 0;
      n.lastSeen = now;
      if (latencyS !== undefined) n.latencyEwma = a * latencyS + (1 - a) * n.latencyEwma;
    } else {
      n.consecutiveFailures += 1;
    }
    recompute(n, now, this.#p);
  }

  sweep(now: number): void {
    for (const n of this.#nodes.values()) recompute(n, now, this.#p);
  }

  status(did: string): string {
    return this.#nodes.get(did)?.status ?? DEAD;
  }

  healthy(exclude: ReadonlySet<string> = new Set()): string[] {
    return [...this.#nodes.values()]
      .filter((n) => n.status === HEALTHY && !exclude.has(n.did))
      .sort((a, b) => a.latencyEwma - b.latencyEwma || b.lastSeen - a.lastSeen)
      .map((n) => n.did);
  }

  pick(count: number, exclude: ReadonlySet<string> = new Set()): string[] {
    return this.healthy(exclude).slice(0, count);
  }
}
