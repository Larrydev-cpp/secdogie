// The TS contributor node's runtime: a real bounded fetch (faked transport) +
// the real WASM route scan produce an output shaped exactly like the Python
// worker, and a signed result the core verifies. Needs the built wasm
// (npm run build:wasm); runtime-tests builds it.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { type CanonObject, sha256Hex } from '../src/core/canon.ts';
import { WebCryptoSigner } from '../src/core/ed25519.ts';
import { signPayload, verifyPayload } from '../src/core/envelope.ts';
import { makePolicy } from '../src/sandbox/policy.ts';
import { crawl, runTask } from '../src/swarm/node.ts';
import { outputDigest } from '../src/swarm/tasks.ts';
import { DOCS, PAGES, loadEngine } from './engine.ts';

const htmlFetch =
  (pages: Record<string, string>) =>
  async (url: string): Promise<Response> => {
    const body = pages[url];
    if (body === undefined) return new Response('not found', { status: 404 });
    return new Response(body, { status: 200, headers: { 'content-type': 'text/html' } });
  };

test('a node crawls a public page into a worker-shaped output', async () => {
  const engine = await loadEngine();
  const policy = makePolicy({ allowedOrigins: [DOCS] });
  const output = await crawl(policy, engine, `${DOCS}/`, htmlFetch(PAGES));

  assert.equal(output['kind'], 'page');
  assert.equal(output['status'], 200);
  assert.equal(output['content_hash'], await sha256Hex(new TextEncoder().encode(PAGES[`${DOCS}/`]!)));
  const doc = output['document'] as CanonObject;
  assert.equal(doc['origin'], DOCS);
  const cands = output['candidates'] as CanonObject[];
  assert.ok(cands.length >= 1, 'found candidate states');
  for (const c of cands) {
    assert.match(c['key'] as string, /^[0-9a-f]{64}$/);
    assert.equal(c['origin'], DOCS); // only on-origin candidates
  }
  const keys = cands.map((c) => c['key'] as string);
  assert.deepEqual(keys, [...keys].sort(), 'candidates sorted by key');
});

test('runTask verifies and signs a result the core accepts', async () => {
  const engine = await loadEngine();
  const core = await WebCryptoSigner.generate();
  const node = await WebCryptoSigner.generate();

  const task = await signPayload(core, {
    protocol: 'secdogie/swarm/v1',
    task_id: 'task-xyz',
    kind: 'crawl',
    spec: { url: `${DOCS}/`, allowed_origins: [DOCS] },
    issued_at_ms: 0,
    deadline_ms: 10000,
    redundancy: 1,
    epoch: 1,
  });
  // the node would check the core's signature against a trust set first:
  assert.ok((await verifyPayload(task)).signer === core.did);

  const signed = await runTask(node, task, engine, 1234, htmlFetch(PAGES));
  const v = await verifyPayload(signed);
  assert.ok(v.ok && v.signer === node.did, 'result signature valid');
  assert.equal(signed['node_did'], node.did);
  assert.equal(signed['task_id'], 'task-xyz');
  assert.match(await outputDigest(signed['output'] as CanonObject), /^[0-9a-f]{64}$/);
});

test('a refused URL yields an empty, still-valid output', async () => {
  const engine = await loadEngine();
  const policy = makePolicy({ allowedOrigins: [DOCS] });
  const output = await crawl(policy, engine, 'https://evil.test/x', htmlFetch(PAGES));
  assert.equal(output['kind'], 'refused');
  assert.deepEqual(output['candidates'], []);
  assert.equal(output['document'], null);
});
