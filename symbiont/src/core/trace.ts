/**
 * DevTools only. Everything mechanical -- DIDs, fingerprints, hashes, link and
 * request ids, byte counts, the spec's message names (ATTACH_OPERATOR,
 * CURRENT_STATUS, GRAPH_SNAPSHOT, ADD_GOAL) -- goes here and never into the
 * page. This is the one file allowed to touch `console` (tests/purity.test.ts).
 *
 * Never traced, whatever a caller passes: pairing secrets and MACs, the pairing
 * link, and anything the person typed or the node asked in words. Those fields
 * are dropped by name before anything is printed.
 */

const REDACT = new Set([
  'secret', 'mac', 'fragment', 'hash_fragment', 'link', 'pair',
  'title', 'text', 'content', 'answer', 'question', 'value', 'note', 'typed',
]);

export type TraceFields = Record<string, unknown>;

let sink: (line: string, fields: TraceFields) => void = (line, fields) => {
  console.debug(`[symbiont] ${line}`, fields);
};

/** One DevTools line. `fields` are copied with the redacted names removed. */
export function trace(event: string, fields: TraceFields = {}): void {
  const safe: TraceFields = {};
  for (const [k, v] of Object.entries(fields)) {
    if (!REDACT.has(k)) safe[k] = v;
  }
  try {
    sink(event, safe);
  } catch {
    // DevTools output must never break the page
  }
}

/** Tests capture the trace instead of the console. Returns the restore function. */
export function captureTrace(into: (line: string, fields: TraceFields) => void): () => void {
  const prev = sink;
  sink = into;
  return () => {
    sink = prev;
  };
}
