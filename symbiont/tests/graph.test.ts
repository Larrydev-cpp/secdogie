// Subsystem A through the WASM engine: DAG insertion, append-only refusal,
// have/want convergence, the lexical route adapter, and the topology the
// gates read.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { type CanonObject, canonicalText, fromHex, parseLossless } from '../src/core/canon.ts';
import { WebCryptoSigner } from '../src/core/ed25519.ts';
import { signPayload } from '../src/core/envelope.ts';
import { DELTA_TYPE, TopologyEngine } from '../src/graph/topology.ts';
import { DOCS, FORUM, PAGES, loadEngine } from './engine.ts';

const vectors = JSON.parse(readFileSync(new URL('../../fixtures/vectors/graph_delta.json', import.meta.url), 'utf8'));

async function delta(agent: WebCryptoSigner, parents: string[], lamport: number, ops: CanonObject[]): Promise<string> {
  return canonicalText(await signPayload(agent, { type: DELTA_TYPE, author: agent.did, parents, lamport, ops }));
}

const state = (route: string): CanonObject => ({ op: 'add_state', origin: DOCS, route, query_keys: [] });

test('DAG node insertion: a signed delta goes in, heads advance, an orphan waits for its parent', async () => {
  const engine = await loadEngine();
  const agent = await WebCryptoSigner.fromSeed(fromHex(vectors.agent.seed_hex));
  const g = engine.createGraph([agent.did]);

  // the Python-signed vectors, child first
  const [root, child] = vectors.valid;
  assert.deepEqual(g.ingest(child.wire), { outcome: 'pending', cid: child.cid, missing: [root.cid] });
  const r = g.ingest(root.wire);
  assert.equal(r.outcome, 'inserted');
  assert.ok(r.outcome === 'inserted' && r.promoted[0] === child.cid);
  assert.deepEqual(g.frontier(), { heads: [child.cid], count: 2, nextLamport: 3 });

  // a TS-signed delta on top
  const w = await delta(agent, [child.cid], 3, [state('/new')]);
  const out = g.ingest(w);
  assert.equal(out.outcome, 'inserted');
  assert.equal(g.frontier().heads.length, 1);
  assert.equal(g.ingest(w).outcome, 'duplicate');
});

test('the engine refuses tombstones, bad clocks, forgeries and strangers itself', async () => {
  const engine = await loadEngine();
  const agent = await WebCryptoSigner.generate();
  const stranger = await WebCryptoSigner.generate();
  const g = engine.createGraph([agent.did]);
  g.ingest(await delta(agent, [], 1, [state('/a')]));
  const [head] = g.frontier().heads;

  assert.throws(() => g.ingest('not json'), /not canonical JSON/);
  const cases: Array<[Promise<string>, RegExp]> = [
    [delta(agent, [head!], 2, [{ op: 'remove_state', state: 'x' }]), /unknown op/],
    [delta(agent, [head!], 2, [{ op: 'tombstone', state: 'x' }]), /unknown op/],
    [delta(agent, [head!], 9, [state('/b')]), /lamport/],
    [delta(stranger, [], 1, [state('/c')]), /untrusted author/],
    [delta(agent, [], 1, [{ op: 'add_state', origin: 'http://docs.example.com', route: '/', query_keys: [] }]), /origin/],
  ];
  for (const [w, why] of cases) {
    const wire = await w;
    assert.throws(() => g.ingest(wire), why, wire.slice(0, 80));
  }
  const good = parseLossless(await delta(agent, [head!], 2, [state('/d')])) as Record<string, unknown>;
  const forged = canonicalText({ ...(good as CanonObject), lamport: 3 });
  assert.throws(() => g.ingest(forged), /invalid signature/);
  assert.throws(() => engine.createGraph([]), /trust/);
});

test('two graphs converge over have/want', async () => {
  const engine = await loadEngine();
  const agent = await WebCryptoSigner.generate();
  const a = engine.createGraph([agent.did]);
  const b = engine.createGraph([agent.did]);
  let prev: string[] = [];
  for (let i = 1; i <= 6; i++) {
    a.ingest(await delta(agent, prev, i, [state(`/p${i}`)]));
    prev = a.frontier().heads;
  }
  const want = b.onHave(a.have().heads);
  assert.ok(want);
  const reply = a.onWant(want.cids, want.have_heads);
  assert.equal(reply.envelopes.length, 6, 'the whole missing history, parents first');
  const { report, want: more } = b.onDeltas(reply.envelopes);
  assert.equal(report.inserted.length, 6);
  assert.equal(more, null);
  assert.deepEqual(b.frontier().heads, a.frontier().heads);
  assert.equal(b.onHave(a.have().heads), null);
});

test('the route adapter emits candidate states, and the topology records them as signed deltas', async () => {
  const engine = await loadEngine();
  const agent = await WebCryptoSigner.generate();
  const topo = new TopologyEngine({ graph: engine.createGraph([agent.did]), agent });
  const allowed = [DOCS, FORUM];
  const scan = engine.scanRoutes({ document_url: `${DOCS}/account/settings`, allowed_origins: allowed }, PAGES[`${DOCS}/account/settings`]!);
  assert.equal(scan.candidates.length, 1, 'the login form has a password field and is skipped');
  assert.deepEqual(scan.candidates[0]!.fields, ['confirm', 'csrf', 'reason']);
  assert.equal(scan.skipped_password_forms, 1);
  assert.ok(!JSON.stringify(scan).includes('tok-123'), 'field values never leave the page');

  for (const url of [`${DOCS}/`, `${DOCS}/account/settings`, `${FORUM}/`]) {
    const s = engine.scanRoutes({ document_url: url, allowed_origins: allowed }, PAGES[url]!);
    await topo.record({ scan: s, contentHash: 'ab'.repeat(32), byteLen: PAGES[url]!.length });
  }
  const before = topo.graph.frontier().count;
  const again = engine.scanRoutes({ document_url: `${DOCS}/`, allowed_origins: allowed }, PAGES[`${DOCS}/`]!);
  await topo.record({ scan: again, contentHash: 'ab'.repeat(32), byteLen: PAGES[`${DOCS}/`]!.length });
  assert.equal(topo.graph.frontier().count, before, 're-reading an unchanged page adds nothing');

  const snap = topo.snapshot();
  const deletes = snap.targets.filter((t) => t.route === '/account/delete');
  assert.equal(deletes.length, 2);
  assert.ok(deletes.every((t) => t.form?.method === 'post'));
  const plan = topo.graph.planAction(deletes.find((t) => t.origin === DOCS)!.stateKey, 'submit');
  assert.equal(plan.risk, 'irreversible');
  assert.equal(plan.target_action.high_risk, true);
  assert.throws(() => topo.graph.planAction('0'.repeat(64), 'navigate'), /not in the graph/);
  assert.equal(engine.canonical('[1.0,1e16,{"b":1,"a":-0}]'), '[1.0,1e+16,{"a":0,"b":1}]');
});
