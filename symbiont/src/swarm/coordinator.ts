/**
 * The core-side coordinator for a Cloudflare Worker, mirroring
 * `secdogie_swarm.coordinator`: assign a task to several nodes, migrate off a
 * failing node before its deadline, collect signed results, decide by quorum,
 * and emit a core-signed consensus record to anchor. Transport-injected
 * (`dispatch`); `tick(now)` drives churn. Verifying a result is async (the
 * digest hashes), so `onResult` is async.
 */

import { type CanonObject } from '../core/canon.ts';
import { type Signer } from '../core/ed25519.ts';
import { TrustSet, signPayload } from '../core/envelope.ts';
import { Tally, quorumFor, verifyResult } from './consensus.ts';
import { NodeRegistry } from './registry.ts';
import { PROTOCOL, assertPublic, buildCrawl, signTask, validateTask } from './tasks.ts';
import * as wire from './wire.ts';

export const PENDING = 'pending';
export const DECIDED = 'decided';
export const FAILED = 'failed';

interface Job {
  task: CanonObject;
  signedTask: CanonObject;
  quorum: number;
  assigned: Map<string, number>;
  ever: Set<string>;
  returned: Set<string>;
  tally: Tally;
  firstOutput: Map<string, CanonObject>;
  migrations: number;
  state: string;
  checkpoint: CanonObject | null;
}

export interface CoordinatorOpts {
  dispatch: (nodeDid: string, message: CanonObject) => void;
  assignTimeoutS?: number;
  maxMigrations?: number;
  onDecided?: (record: CanonObject) => void;
  onFailed?: (taskId: string, reason: string) => void;
}

export class SwarmCoordinator {
  readonly registry: NodeRegistry;
  readonly #core: Signer;
  readonly #dispatch: (nodeDid: string, m: CanonObject) => void;
  readonly #assignTimeoutS: number;
  readonly #maxMigrations: number;
  readonly #onDecided?: (record: CanonObject) => void;
  readonly #onFailed?: (taskId: string, reason: string) => void;
  readonly #enrolled = new Set<string>();
  readonly #jobs = new Map<string, Job>();
  #seq = 0;

  constructor(core: Signer, registry: NodeRegistry, opts: CoordinatorOpts) {
    this.#core = core;
    this.registry = registry;
    this.#dispatch = opts.dispatch;
    this.#assignTimeoutS = opts.assignTimeoutS ?? 8;
    this.#maxMigrations = opts.maxMigrations ?? 3;
    this.#onDecided = opts.onDecided;
    this.#onFailed = opts.onFailed;
  }

  enroll(nodeDid: string, now: number): void {
    this.#enrolled.add(nodeDid);
    this.registry.heartbeat(nodeDid, now);
  }

  #trust(): TrustSet | null {
    return this.#enrolled.size > 0 ? new TrustSet(this.#enrolled) : null;
  }

  async submitCrawl(url: string, allowedOrigins: string[], now: number, redundancy = 3): Promise<string> {
    this.#seq += 1;
    const task = buildCrawl({
      taskId: `task-${this.#seq.toString(16).padStart(8, '0')}`,
      url,
      allowedOrigins,
      issuedAtMs: Math.round(now * 1000),
      deadlineMs: 10000,
      redundancy,
      epoch: 1,
    });
    return this.submit(task, now);
  }

  async submit(task: CanonObject, now: number): Promise<string> {
    validateTask(task);
    assertPublic(task);
    const signed = await signTask(this.#core, task);
    const job: Job = {
      task,
      signedTask: signed,
      quorum: quorumFor(task['redundancy'] as number),
      assigned: new Map(),
      ever: new Set(),
      returned: new Set(),
      tally: new Tally(),
      firstOutput: new Map(),
      migrations: 0,
      state: PENDING,
      checkpoint: null,
    };
    this.#jobs.set(task['task_id'] as string, job);
    this.#assign(job, task['redundancy'] as number, now);
    this.#checkFeasible(job);
    return task['task_id'] as string;
  }

  #send(job: Job, node: string, now: number): void {
    job.assigned.set(node, now + this.#assignTimeoutS);
    job.ever.add(node);
    this.#dispatch(node, wire.dispatch(job.signedTask, job.checkpoint ?? undefined));
  }

  #assign(job: Job, count: number, now: number): void {
    for (const did of this.registry.pick(count, job.ever)) this.#send(job, did, now);
  }

  async onResult(result: CanonObject, now: number): Promise<void> {
    const taskId = typeof result['task_id'] === 'string' ? (result['task_id'] as string) : null;
    const job = taskId ? this.#jobs.get(taskId) : undefined;
    if (!job || job.state !== PENDING || taskId === null) return;
    const trust = this.#trust();
    if (trust === null) return;
    const v = await verifyResult(result, taskId, job.ever, trust);
    if (!v.ok || v.nodeDid === null || v.digest === null) {
      if (v.nodeDid !== null && this.registry.has(v.nodeDid)) this.registry.record(v.nodeDid, false, now);
      return;
    }
    if (job.returned.has(v.nodeDid)) return;
    job.assigned.delete(v.nodeDid);
    job.returned.add(v.nodeDid);
    this.registry.record(v.nodeDid, true, now);
    job.tally.add(v.nodeDid, v.digest);
    if (!job.firstOutput.has(v.digest)) job.firstOutput.set(v.digest, result['output'] as CanonObject);
    await this.#tryDecide(job, now);
    if (job.state === PENDING) this.#checkFeasible(job);
  }

  async #tryDecide(job: Job, now: number): Promise<void> {
    const decided = job.tally.decide(job.quorum);
    if (decided === null) return;
    job.state = DECIDED;
    for (const loser of job.tally.outliers(decided.digest)) this.registry.record(loser, false, now);
    const record = await signPayload(this.#core, {
      protocol: PROTOCOL,
      task_id: job.task['task_id'] as string,
      epoch: job.task['epoch'] as number,
      digest: decided.digest,
      output: job.firstOutput.get(decided.digest) as CanonObject,
      nodes: [...decided.agree].sort(),
      decided_at_ms: Math.round(now * 1000),
    });
    this.#onDecided?.(record);
  }

  onCheckpoint(taskId: string, nodeDid: string, blob: CanonObject): void {
    const job = this.#jobs.get(taskId);
    if (!job || job.state !== PENDING || !job.ever.has(nodeDid)) return;
    assertPublic({ checkpoint: blob });
    job.checkpoint = blob;
  }

  tick(now: number): void {
    this.registry.sweep(now);
    for (const job of this.#jobs.values()) {
      if (job.state !== PENDING) continue;
      for (const [did, deadline] of [...job.assigned]) {
        const status = this.registry.status(did);
        if (status === 'suspect' || status === 'dead' || now > deadline) {
          job.assigned.delete(did);
          this.registry.record(did, false, now);
          if (job.migrations < this.#maxMigrations) {
            const repl = this.registry.pick(1, job.ever);
            if (repl.length > 0) {
              job.migrations += 1;
              this.#send(job, repl[0]!, now);
            }
          }
        }
      }
      this.#checkFeasible(job);
    }
  }

  #checkFeasible(job: Job): void {
    if (job.state !== PENDING) return;
    if (job.assigned.size === 0 && job.tally.decide(job.quorum) === null) {
      job.state = FAILED;
      this.#onFailed?.(job.task['task_id'] as string, 'no quorum: nodes dropped or disagreed');
    }
  }

  stateOf(taskId: string): string | undefined {
    return this.#jobs.get(taskId)?.state;
  }
}
