import { StateGraphStore, vertexId } from "./graph.js";
import { type PublicFetchPolicy, sanitizePublicUrl } from "./public-source.js";
import type {
  PublicFetchTask,
  RouteEvidence,
  StateGraphDelta,
  SwarmDeltaEnvelope,
  SwarmHave,
  SwarmWant,
  StateVertex,
} from "./protocol.js";

export interface WorkerLike {
  postMessage(message: unknown): void;
  terminate?(): void;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror?: ((event: ErrorEvent) => void) | null;
}

export interface WorkerFactory {
  spawn(): WorkerLike;
}

export interface PeerTransport {
  readonly localNodeId: string;
  send(peerId: string, message: SwarmDeltaEnvelope | SwarmHave | SwarmWant): Promise<void>;
  onMessage(callback: (message: SwarmDeltaEnvelope | SwarmHave | SwarmWant) => void): () => void;
}

interface WorkResponse {
  readonly type: "analyzed";
  readonly taskId: string;
  readonly url: string;
  readonly sourceHash: string;
  readonly routes: readonly RouteEvidence[];
  readonly error?: string;
}

/** A local worker pool; the swarm transport stays separate so WebRTC is optional. */
export class TopologyWorkerPool {
  private readonly workers: WorkerLike[];
  private next = 0;
  private readonly pending = new Map<string, {
    resolve: (response: WorkResponse) => void;
    reject: (error: Error) => void;
    worker: WorkerLike;
  }>();

  constructor(factory: WorkerFactory, size: number) {
    if (!Number.isInteger(size) || size < 1) throw new RangeError("worker pool size must be >= 1");
    this.workers = Array.from({ length: size }, () => factory.spawn());
    this.workers.forEach((worker) => {
      worker.onmessage = (event) => {
        const response = event.data as Partial<WorkResponse>;
        if (response?.type !== "analyzed" || typeof response.taskId !== "string") return;
        const pending = this.pending.get(response.taskId);
        if (!pending) return;
        this.pending.delete(response.taskId);
        pending.resolve(response as WorkResponse);
      };
      worker.onerror = (event) => {
        const error = event.error instanceof Error ? event.error : new Error(event.message || "worker failed");
        for (const [taskId, pending] of this.pending) {
          if (pending.worker !== worker) continue;
          this.pending.delete(taskId);
          pending.reject(error);
        }
      };
    });
  }

  analyze(task: PublicFetchTask, policy: PublicFetchPolicy): Promise<WorkResponse> {
    const worker = this.workers[this.next++ % this.workers.length]!;
    return new Promise((resolve, reject) => {
      this.pending.set(task.taskId, { resolve, reject, worker });
      try {
        worker.postMessage({ type: "analyze", task, policy });
      } catch (error) {
        this.pending.delete(task.taskId);
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }

  close(): void {
    const error = new Error("topology worker pool closed");
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
    for (const worker of this.workers) worker.terminate?.();
  }
}

export class SwarmCoordinator {
  private readonly unsubscribe: () => void;
  private readonly seenDeltaHashes = new Set<string>();
  private readonly deltaLog = new Map<string, StateGraphDelta>();

  constructor(
    private readonly graph: StateGraphStore,
    private readonly transport: PeerTransport,
    private readonly workers: TopologyWorkerPool,
    private readonly fetchPolicy: PublicFetchPolicy,
  ) {
    this.unsubscribe = transport.onMessage((message) => void this.onMessage(message));
  }

  async explore(task: PublicFetchTask): Promise<StateGraphDelta> {
    sanitizePublicUrl(task.url, this.fetchPolicy);
    const analyzed = await this.workers.analyze(task, this.fetchPolicy);
    if (analyzed.error) throw new Error(`public topology task failed: ${analyzed.error}`);
    const vertices: StateVertex[] = analyzed.routes.map((route) => ({
      id: vertexId(route.path, route.sourceHash),
      route: route.path,
      sourceHash: route.sourceHash,
    }));
    // Route literals establish candidate states, not transitions. A transition is
    // added only when a parser supplies explicit navigation evidence; lexical
    // ordering is not a valid state machine.
    const delta = await this.graph.makeDelta({ vertices }, this.transport.localNodeId);
    await this.graph.applyDelta(delta);
    this.seenDeltaHashes.add(delta.deltaHash);
    this.deltaLog.set(delta.deltaHash, delta);
    return delta;
  }

  async publish(peerId: string, delta: StateGraphDelta): Promise<void> {
    this.seenDeltaHashes.add(delta.deltaHash);
    this.deltaLog.set(delta.deltaHash, delta);
    await this.transport.send(peerId, {
      protocol: "secdogie/swarm-delta/v1",
      from: this.transport.localNodeId,
      to: peerId,
      delta,
    });
  }

  makeHave(): SwarmHave {
    return {
      protocol: "secdogie/swarm-have/v1",
      nodeId: this.transport.localNodeId,
      graphId: this.graph.graphId,
      snapshotHash: this.graph.snapshotHash,
      epoch: this.graph.currentEpoch,
    };
  }

  makeWant(): SwarmWant {
    return {
      protocol: "secdogie/swarm-want/v1",
      nodeId: this.transport.localNodeId,
      graphId: this.graph.graphId,
      baseSnapshotHash: this.graph.snapshotHash,
      knownDeltaHashes: [...this.seenDeltaHashes].sort(),
    };
  }

  close(): void {
    this.unsubscribe();
    this.workers.close();
  }

  private async onMessage(message: SwarmDeltaEnvelope | SwarmHave | SwarmWant): Promise<void> {
    try {
      if (message.protocol === "secdogie/swarm-delta/v1") {
        if (message.to !== this.transport.localNodeId) return;
        if (this.seenDeltaHashes.has(message.delta.deltaHash)) return;
        const status = await this.graph.applyDelta(message.delta);
        if (status === "applied") {
          this.seenDeltaHashes.add(message.delta.deltaHash);
          this.deltaLog.set(message.delta.deltaHash, message.delta);
        }
        return;
      }
      if (message.protocol === "secdogie/swarm-have/v1") {
        if (message.graphId !== this.graph.graphId || message.nodeId === this.transport.localNodeId) return;
        await this.transport.send(message.nodeId, this.makeWant());
        return;
      }
      if (message.protocol === "secdogie/swarm-want/v1" && message.graphId === this.graph.graphId) {
        const known = new Set(message.knownDeltaHashes);
        for (const [deltaHash, delta] of this.deltaLog) {
          if (known.has(deltaHash)) continue;
          await this.transport.send(message.nodeId, {
            protocol: "secdogie/swarm-delta/v1",
            from: this.transport.localNodeId,
            to: message.nodeId,
            delta,
          });
        }
      }
    } catch {
      // Remote graph material is untrusted. Invalid hashes/references or a
      // failing peer must not take down the local swarm coordinator.
    }
  }
}
