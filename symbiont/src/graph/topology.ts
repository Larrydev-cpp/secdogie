/**
 * Turns what the sandbox saw into signed, append-only graph deltas, and the
 * graph back into what Gate 1 reasons over.
 *
 * Each scanned page becomes: the page's own state, one state per candidate
 * the markup named, one `add_reference` per candidate (a lexical reference,
 * never a claimed transition), and one `add_observation` binding the page to
 * the hash of the bytes that were read. The agent's key signs; the engine
 * verifies that signature itself before anything is inserted.
 */

import { type CanonObject, canonicalText, compareCodePoints } from '../core/canon.ts';
import type { Signer } from '../core/ed25519.ts';
import { signPayload } from '../core/envelope.ts';
import type { TopologySnapshot, TopologyTarget } from '../gate1/interpret.ts';
import type { TopologyStatus } from '../stream/stream.ts';
import type { IngestOutcome, ScanResult, StateGraph, ViewReference } from './wasm.ts';

export const DELTA_TYPE = 'secdogie/state-graph-delta/v1';
const MAX_OPS = 256;

export interface PageRead {
  readonly scan: ScanResult;
  /** sha256 hex of the body bytes that were read. */
  readonly contentHash: string;
  readonly byteLen: number;
}

export class TopologyEngine {
  readonly graph: StateGraph;
  readonly #agent: Signer;
  readonly #clock: () => number;
  #recent: Array<{ at: number; n: number }> = [];

  constructor(opts: { graph: StateGraph; agent: Signer; clock?: () => number }) {
    this.graph = opts.graph;
    this.#agent = opts.agent;
    this.#clock = opts.clock ?? (() => Date.now());
  }

  /** Records one page read as one or more deltas. Returns how many new states it added. */
  async record(page: PageRead): Promise<{ outcomes: IngestOutcome[]; newStates: number }> {
    const doc = page.scan.document;
    if (doc === null) throw new Error('scan has no document state');
    // Only what the graph does not hold yet: re-reading an unchanged page adds nothing.
    const view = this.graph.view();
    const before = new Set(view.states.map((s) => s.key));
    const known = new Set(view.references.map((r) => `${r.from}|${r.to}|${r.via}|${r.method}|${r.fields.join(',')}`));
    const observed = view.states
      .find((s) => s.key === doc.key)
      ?.observations.some((o) => o.content_hash === page.contentHash && o.byte_len === page.byteLen);
    const ops: CanonObject[] = [];
    const seen = new Set<string>(before);
    const addState = (origin: string, route: string, queryKeys: readonly string[], key: string) => {
      if (seen.has(key)) return;
      seen.add(key);
      ops.push({ op: 'add_state', origin, route, query_keys: [...queryKeys] });
    };
    addState(doc.origin, doc.route, doc.query_keys, doc.key);
    for (const c of page.scan.candidates) addState(c.origin, c.route, c.query_keys, c.key);
    const refs = new Set<string>();
    for (const c of page.scan.candidates) {
      const fields = c.via === 'form' ? [...c.fields] : [];
      const method = c.via === 'form' ? c.method : 'get';
      const id = `${doc.key}|${c.key}|${c.via}|${method}|${fields.join(',')}`;
      if (refs.has(id) || known.has(id)) continue;
      refs.add(id);
      ops.push({ op: 'add_reference', from: doc.key, to: c.key, via: c.via, method, fields });
    }
    if (!observed) {
      ops.push({ op: 'add_observation', state: doc.key, content_hash: page.contentHash, byte_len: page.byteLen });
    }

    const outcomes: IngestOutcome[] = [];
    for (let i = 0; i < ops.length; i += MAX_OPS) {
      const { heads, nextLamport } = this.graph.frontier();
      const payload: CanonObject = {
        type: DELTA_TYPE,
        author: this.#agent.did,
        parents: [...heads].sort(),
        lamport: nextLamport,
        ops: ops.slice(i, i + MAX_OPS),
      };
      const envelope = await signPayload(this.#agent, payload);
      outcomes.push(this.graph.ingest(canonicalText(envelope)));
    }
    const newStates = this.graph.view().states.filter((s) => !before.has(s.key)).length;
    if (newStates > 0) this.#recent.push({ at: this.#clock(), n: newStates });
    return { outcomes, newStates };
  }

  /** What Gate 1 may choose among: the graph's states, with the forms that reach them. */
  snapshot(): TopologySnapshot {
    const view = this.graph.view();
    const forms = new Map<string, ViewReference>();
    for (const r of view.references) {
      if (r.via !== 'form') continue;
      const prev = forms.get(r.to);
      // Same deterministic choice as graph/src/plan.rs: the greatest reference wins.
      if (!prev || compareRef(r, prev) > 0) forms.set(r.to, r);
    }
    const targets: TopologyTarget[] = view.states.map((s) => {
      const f = forms.get(s.key);
      return {
        stateKey: s.key,
        origin: s.origin,
        route: s.route,
        queryKeys: s.query_keys,
        form: f ? { method: f.method, fields: f.fields } : null,
      };
    });
    return { targets };
  }

  status(windowMs = 60_000): TopologyStatus {
    const view = this.graph.view();
    const now = this.#clock();
    this.#recent = this.#recent.filter((r) => now - r.at <= windowMs);
    return {
      origins: new Set(view.states.map((s) => s.origin)).size,
      states: view.states.length,
      references: view.references.length,
      recentStates: this.#recent.reduce((a, r) => a + r.n, 0),
    };
  }
}

/** Rust's derived Ord on Reference: (from, to, via, method, fields). */
function compareRef(a: ViewReference, b: ViewReference): number {
  const VIA = ['a', 'area', 'form', 'link', 'redirect'];
  const c =
    cmp(a.from, b.from) || cmp(a.to, b.to) || VIA.indexOf(a.via) - VIA.indexOf(b.via) || cmp(a.method, b.method);
  if (c) return c;
  for (let i = 0; i < Math.min(a.fields.length, b.fields.length); i++) {
    const d = cmp(a.fields[i]!, b.fields[i]!);
    if (d) return d;
  }
  return a.fields.length - b.fields.length;
}

const cmp = compareCodePoints;
