/**
 * The connection topology for the Workers core, mirroring
 * `secdogie_swarm.topology`: three layers -- the central core, a line of P2P
 * nodes, and the user.
 *
 *   - the core attaches to the EDGE nodes (the outermost healthy nodes at each
 *     end), holding up to `edgeDepth` per side as warm, live connections, so one
 *     edge failing never leaves a gap -- the next is already attached;
 *   - the user attaches only to an INNER node, never an edge, so the user is at
 *     least one hop from an edge and two from the core, and cannot reach it;
 *   - when an edge degrades, the edge moves inward continuously. A SUSPECT node
 *     is already excluded from `healthy`, so the switch is predictive, not only
 *     after a hard timeout.
 *
 * `edgeDepth` is the warm redundancy the core wants per side (default 1); with
 * depth >= 2 a hot standby is already connected, so failover is gap-free. It is
 * auto-reduced on a short line so the user keeps at least `minInner` inner
 * nodes: redundancy never costs the isolation buffer.
 *
 * A role layer over `NodeRegistry` health -- it produces no wire bytes, so it
 * needs no golden vectors; it just has to decide the same roles the Python core
 * does from the same health.
 */

import { HEALTHY, NodeRegistry } from './registry.ts';

export class LineTopology {
  readonly #registry: NodeRegistry;
  readonly #edgeDepth: number;
  readonly #minInner: number;
  readonly #line: string[] = [];

  constructor(registry: NodeRegistry, opts: { edgeDepth?: number; minInner?: number } = {}) {
    this.#registry = registry;
    this.#edgeDepth = Math.max(1, opts.edgeDepth ?? 1);
    this.#minInner = Math.max(0, opts.minInner ?? 1);
  }

  place(nodeDid: string): void {
    if (!this.#line.includes(nodeDid)) this.#line.push(nodeDid);
  }

  remove(nodeDid: string): void {
    const i = this.#line.indexOf(nodeDid);
    if (i >= 0) this.#line.splice(i, 1);
  }

  has(nodeDid: string): boolean {
    return this.#line.includes(nodeDid);
  }

  #healthy(): string[] {
    return this.#line.filter((d) => this.#registry.status(d) === HEALTHY);
  }

  #depthFor(n: number): number {
    let d = this.#edgeDepth;
    while (d > 1 && n - 2 * d < this.#minInner) d -= 1;
    return Math.min(d, Math.floor((n + 1) / 2));
  }

  edges(depth?: number): string[] {
    const h = this.#healthy();
    if (h.length === 0) return [];
    const k = depth === undefined ? this.#depthFor(h.length) : Math.min(Math.max(1, depth), Math.floor((h.length + 1) / 2));
    const out: string[] = [];
    for (const d of [...h.slice(0, k), ...h.slice(h.length - k)]) if (!out.includes(d)) out.push(d);
    return out;
  }

  inner(): string[] {
    const e = new Set(this.edges());
    return this.#healthy().filter((d) => !e.has(d));
  }

  standbys(): string[] {
    const outer = new Set(this.edges(1));
    return this.edges().filter((d) => !outer.has(d));
  }

  coreAttach(): string[] {
    return this.edges();
  }

  userEntry(exclude: ReadonlySet<string> = new Set()): string | null {
    const h = this.#healthy();
    const inner = this.inner().filter((d) => !exclude.has(d));
    if (inner.length === 0) return null;
    const center = (h.length - 1) / 2;
    inner.sort((a, b) => Math.abs(h.indexOf(a) - center) - Math.abs(h.indexOf(b) - center));
    return inner[0]!;
  }

  userIsIsolatedFromCore(): boolean {
    const e = new Set(this.edges());
    return !this.inner().some((d) => e.has(d));
  }

  roles(): { edges: string[]; standbys: string[]; inner: string[]; userEntry: string | null; isolated: boolean } {
    return {
      edges: this.edges(),
      standbys: this.standbys(),
      inner: this.inner(),
      userEntry: this.userEntry(),
      isolated: this.userIsIsolatedFromCore(),
    };
  }
}
