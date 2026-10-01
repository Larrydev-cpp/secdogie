import type {
  HashHex,
  StateEdge,
  StateGraphDelta,
  StateGraphSnapshot,
  StateVertex,
} from "./protocol.js";

export function canonicalJson(value: unknown): string {
  if (value === undefined) return "null";
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`)
    .join(",")}}`;
}

export async function sha256Hex(value: string | Uint8Array): Promise<HashHex> {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function vertexId(route: string, sourceHash: HashHex): string {
  return `${route}#${sourceHash}`;
}

export function edgeId(from: string, to: string, evidence: HashHex): string {
  return `${from}->${to}#${evidence}`;
}

function sortVertices(items: Iterable<StateVertex>): StateVertex[] {
  return [...items].sort((a, b) => a.id.localeCompare(b.id));
}

function sortEdges(items: Iterable<StateEdge>): StateEdge[] {
  return [...items].sort((a, b) => a.id.localeCompare(b.id));
}

export class StateGraphStore {
  private vertices = new Map<string, StateVertex>();
  private edges = new Map<string, StateEdge>();
  private epoch = 0;
  private currentHash: HashHex | null = null;
  private readonly seenDeltaHashes = new Set<HashHex>();

  constructor(public readonly graphId: string) {}

  snapshot(): Omit<StateGraphSnapshot, "snapshotHash"> {
    return {
      graphId: this.graphId,
      epoch: this.epoch,
      parentSnapshotHash: this.currentHash,
      vertices: sortVertices(this.vertices.values()),
      edges: sortEdges(this.edges.values()),
      generatedAtMs: Date.now(),
    };
  }

  async commitSnapshot(nowMs = Date.now()): Promise<StateGraphSnapshot> {
    const material = {
      graphId: this.graphId,
      epoch: this.epoch,
      parentSnapshotHash: this.currentHash,
      vertices: sortVertices(this.vertices.values()),
      edges: sortEdges(this.edges.values()),
      generatedAtMs: nowMs,
    };
    const content = { ...material };
    delete (content as { generatedAtMs?: number }).generatedAtMs;
    const snapshotHash = await sha256Hex(canonicalJson(content));
    this.currentHash = snapshotHash;
    return { ...material, snapshotHash };
  }

  async makeDelta(
    additions: { vertices?: readonly StateVertex[]; edges?: readonly StateEdge[] },
    sourceNodeId: string,
    nowMs = Date.now(),
  ): Promise<StateGraphDelta> {
    const addedVertices = additions.vertices ?? [];
    const addedEdges = additions.edges ?? [];
    const material = {
      graphId: this.graphId,
      epoch: this.epoch + 1,
      baseSnapshotHash: this.currentHash ?? "",
      sourceNodeId,
      addedVertices: sortVertices(addedVertices),
      addedEdges: sortEdges(addedEdges),
      removedVertexIds: [] as string[],
      removedEdgeIds: [] as string[],
      createdAtMs: nowMs,
    };
    const content = { ...material };
    delete (content as { createdAtMs?: number }).createdAtMs;
    return { ...material, deltaHash: await sha256Hex(canonicalJson(content)) };
  }

  async applyDelta(delta: StateGraphDelta): Promise<"applied" | "duplicate" | "stale"> {
    if (delta.graphId !== this.graphId) return "stale";
    if (this.seenDeltaHashes.has(delta.deltaHash)) return "duplicate";
    if (delta.removedVertexIds.length || delta.removedEdgeIds.length) {
      throw new Error("state-graph v1 is append-only; removals require tombstone semantics");
    }
    const content = { ...delta };
    delete (content as { deltaHash?: HashHex; createdAtMs?: number }).deltaHash;
    delete (content as { deltaHash?: HashHex; createdAtMs?: number }).createdAtMs;
    const expectedHash = await sha256Hex(canonicalJson(content));
    if (expectedHash !== delta.deltaHash) throw new Error("invalid state-graph delta hash");
    const incomingVertices = new Set(delta.addedVertices.map((vertex) => vertex.id));
    for (const edge of delta.addedEdges) {
      if (!(this.vertices.has(edge.from) || incomingVertices.has(edge.from))) {
        throw new Error(`edge ${edge.id} references missing source vertex`);
      }
      if (!(this.vertices.has(edge.to) || incomingVertices.has(edge.to))) {
        throw new Error(`edge ${edge.id} references missing target vertex`);
      }
    }
    for (const vertex of delta.addedVertices) this.vertices.set(vertex.id, vertex);
    for (const edge of delta.addedEdges) this.edges.set(edge.id, edge);
    this.epoch = Math.max(this.epoch, delta.epoch);
    await this.commitSnapshot(delta.createdAtMs);
    this.seenDeltaHashes.add(delta.deltaHash);
    return "applied";
  }

  get snapshotHash(): HashHex | null {
    return this.currentHash;
  }

  get currentEpoch(): number {
    return this.epoch;
  }
}
