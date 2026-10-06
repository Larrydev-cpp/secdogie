/**
 * The typed face of the Rust-WASM state-graph engine (`graph/`, built for
 * wasm32). The module has no imports: it cannot reach the network, the DOM or
 * the clock -- it only judges bytes it is handed. Requests and responses are
 * JSON over linear memory (see `graph/src/ffi.rs`); an engine-side error comes
 * back as a value and is thrown here as {@link GraphError}.
 *
 * Signature verification of every delta happens *inside* the engine
 * (Ed25519 verify_strict, signer == author, a non-empty trusted-author set);
 * this wrapper adds nothing to trust and takes nothing away.
 */

import type { PlanPreview } from '../gate2/issuer.ts';

interface Exports {
  readonly memory: WebAssembly.Memory;
  sd_alloc(len: number): number;
  sd_free(ptr: number, len: number): void;
  sd_graph_new(ptr: number, len: number): bigint;
  sd_graph_call(handle: number, ptr: number, len: number): bigint;
  sd_graph_drop(handle: number): void;
  sd_route_scan(cfgPtr: number, cfgLen: number, bodyPtr: number, bodyLen: number): bigint;
  sd_canonical(ptr: number, len: number): bigint;
}

export class GraphError extends Error {}

export type Via = 'a' | 'area' | 'form' | 'link' | 'redirect';

export interface CandidateState {
  readonly origin: string;
  readonly route: string;
  readonly query_keys: readonly string[];
  readonly key: string;
  readonly via: Via;
  readonly method: 'get' | 'post';
  readonly fields: readonly string[];
  readonly offset: number;
}

export interface ScanResult {
  readonly document: { origin: string; route: string; query_keys: readonly string[]; key: string } | null;
  readonly candidates: readonly CandidateState[];
  readonly external: number;
  readonly rejected: Readonly<Record<string, number>>;
  readonly skipped_password_forms: number;
  readonly truncated: boolean;
}

export interface ScanConfig {
  readonly document_url: string;
  readonly allowed_origins: readonly string[];
  readonly max_bytes?: number;
  readonly max_candidates?: number;
}

export type IngestOutcome =
  | { readonly outcome: 'inserted'; readonly cid: string; readonly promoted: readonly string[]; readonly dropped: ReadonlyArray<{ cid: string; reason: string }> }
  | { readonly outcome: 'duplicate'; readonly cid: string }
  | { readonly outcome: 'pending'; readonly cid: string; readonly missing: readonly string[] };

export interface ViewState {
  readonly key: string;
  readonly origin: string;
  readonly route: string;
  readonly query_keys: readonly string[];
  readonly observations: ReadonlyArray<{ content_hash: string; byte_len: number }>;
}

export interface ViewReference {
  readonly from: string;
  readonly to: string;
  readonly via: Via;
  readonly method: 'get' | 'post';
  readonly fields: readonly string[];
  readonly dangling: boolean;
}

export interface TopologyView {
  readonly states: readonly ViewState[];
  readonly references: readonly ViewReference[];
}

export interface WantMessage {
  readonly kind: 'graph_want';
  readonly cids: readonly string[];
  readonly have_heads: readonly string[];
}

export interface DeltasMessage {
  readonly kind: 'graph_deltas';
  readonly envelopes: readonly string[];
}

export interface IngestReport {
  readonly inserted: readonly string[];
  readonly duplicate: number;
  readonly pending: readonly string[];
  readonly rejected: ReadonlyArray<{ index: number; reason: string }>;
}

type Response = { ok: true; [k: string]: unknown } | { ok: false; error: string };

const enc = new TextEncoder();
const dec = new TextDecoder();

export class GraphEngine {
  readonly #x: Exports;
  #poisoned = false;

  private constructor(x: Exports) {
    this.#x = x;
  }

  static async fromBytes(bytes: BufferSource): Promise<GraphEngine> {
    const { instance } = await WebAssembly.instantiate(bytes, {});
    return new GraphEngine(instance.exports as unknown as Exports);
  }

  static async fromUrl(url: string | URL): Promise<GraphEngine> {
    const { instance } = await WebAssembly.instantiateStreaming(fetch(url), {});
    return new GraphEngine(instance.exports as unknown as Exports);
  }

  #put(bytes: Uint8Array): [number, number] {
    const ptr = this.#x.sd_alloc(bytes.length);
    new Uint8Array(this.#x.memory.buffer, ptr, bytes.length).set(bytes);
    return [ptr, bytes.length];
  }

  #take(packed: bigint): Response {
    const ptr = Number(packed >> 32n);
    const len = Number(packed & 0xffffffffn);
    const text = dec.decode(new Uint8Array(this.#x.memory.buffer, ptr, len).slice());
    this.#x.sd_free(ptr, len);
    return JSON.parse(text) as Response;
  }

  /** Calls into the module; a trap poisons the engine (its state is no longer trusted). */
  call(fn: (...ptrs: number[]) => bigint, ...inputs: Uint8Array[]): Response {
    if (this.#poisoned) throw new GraphError('the graph engine trapped earlier and is no longer usable');
    const bufs = inputs.map((b) => this.#put(b));
    try {
      return this.#take(fn(...bufs.flat()));
    } catch (e) {
      if (e instanceof WebAssembly.RuntimeError) this.#poisoned = true;
      throw e;
    } finally {
      for (const [p, l] of bufs) this.#x.sd_free(p, l);
    }
  }

  ok(r: Response): Record<string, unknown> {
    if (!r.ok) throw new GraphError(r.error);
    return r;
  }

  createGraph(trustedAuthors: readonly string[], opts: { maxNodes?: number; maxOrphans?: number } = {}): StateGraph {
    const req: Record<string, unknown> = { trusted_authors: trustedAuthors };
    if (opts.maxNodes !== undefined) req['max_nodes'] = opts.maxNodes;
    if (opts.maxOrphans !== undefined) req['max_orphans'] = opts.maxOrphans;
    const r = this.ok(this.call((p, l) => this.#x.sd_graph_new(p!, l!), enc.encode(JSON.stringify(req))));
    return new StateGraph(this, this.#x, r['handle'] as number);
  }

  /** Runs the lexical route adapter over one page body. */
  scanRoutes(cfg: ScanConfig, body: string | Uint8Array): ScanResult {
    const bytes = typeof body === 'string' ? enc.encode(body) : body;
    const r = this.ok(
      this.call((cp, cl, bp, bl) => this.#x.sd_route_scan(cp!, cl!, bp!, bl!), enc.encode(JSON.stringify(cfg)), bytes),
    );
    return r['scan'] as ScanResult;
  }

  /** The engine's canonical form of a JSON text (a parity check against TS). */
  canonical(text: string): string {
    return this.ok(this.call((p, l) => this.#x.sd_canonical(p!, l!), enc.encode(text)))['canonical'] as string;
  }
}

export class StateGraph {
  readonly #engine: GraphEngine;
  readonly #x: Exports;
  readonly #handle: number;
  #dropped = false;

  constructor(engine: GraphEngine, x: Exports, handle: number) {
    this.#engine = engine;
    this.#x = x;
    this.#handle = handle;
  }

  #call(req: Record<string, unknown>): Record<string, unknown> {
    if (this.#dropped) throw new GraphError('graph dropped');
    return this.#engine.ok(
      this.#engine.call((p, l) => this.#x.sd_graph_call(this.#handle, p!, l!), enc.encode(JSON.stringify(req))),
    );
  }

  /** Inserts one signed envelope (canonical JSON text); the engine verifies it. */
  ingest(wire: string): IngestOutcome {
    return this.#call({ op: 'ingest', wire })['result'] as IngestOutcome;
  }

  frontier(): { heads: string[]; count: number; nextLamport: number } {
    const r = this.#call({ op: 'heads' });
    return { heads: r['heads'] as string[], count: r['count'] as number, nextLamport: r['next_lamport'] as number };
  }

  have(): { kind: 'graph_have'; heads: string[] } {
    return this.#call({ op: 'have' })['message'] as { kind: 'graph_have'; heads: string[] };
  }

  onHave(heads: readonly string[]): WantMessage | null {
    return (this.#call({ op: 'on_have', heads })['want'] as WantMessage | null) ?? null;
  }

  onWant(cids: readonly string[], haveHeads: readonly string[]): DeltasMessage {
    return this.#call({ op: 'on_want', cids, have_heads: haveHeads })['message'] as DeltasMessage;
  }

  onDeltas(envelopes: readonly string[]): { report: IngestReport; want: WantMessage | null } {
    const r = this.#call({ op: 'on_deltas', envelopes });
    return { report: r['report'] as IngestReport, want: (r['want'] as WantMessage | null) ?? null };
  }

  view(): TopologyView {
    return this.#call({ op: 'view' })['view'] as TopologyView;
  }

  /** The deterministic preview of acting on a state already in the graph. */
  planAction(stateKey: string, verb: 'navigate' | 'submit'): PlanPreview {
    return this.#call({ op: 'plan_action', state_key: stateKey, verb })['plan'] as PlanPreview;
  }

  stateKey(origin: string, route: string, queryKeys: readonly string[]): string {
    return this.#call({ op: 'state_key', origin, route, query_keys: queryKeys })['key'] as string;
  }

  drop(): void {
    if (!this.#dropped) {
      this.#x.sd_graph_drop(this.#handle);
      this.#dropped = true;
    }
  }
}
