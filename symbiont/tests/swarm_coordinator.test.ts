// The Workers-side coordinator: quorum on honest nodes, an outlier dropped,
// predictive migration off a silent node, and an honest failure when no quorum
// is reachable -- the same scenarios the Python coordinator proves, so the two
// cores behave identically.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { type CanonObject } from '../src/core/canon.ts';
import { WebCryptoSigner } from '../src/core/ed25519.ts';
import { verifyPayload } from '../src/core/envelope.ts';
import { DECIDED, FAILED, SwarmCoordinator } from '../src/swarm/coordinator.ts';
import { NodeRegistry } from '../src/swarm/registry.ts';
import { outputDigest, signResult } from '../src/swarm/tasks.ts';

const HONEST: CanonObject = { kind: 'page', content_hash: 'abc', candidates: ['/x'] };
const OUTLIER: CanonObject = { kind: 'page', content_hash: 'zzz', candidates: [] };

class Swarm {
  core!: WebCryptoSigner;
  registry!: NodeRegistry;
  coord!: SwarmCoordinator;
  dispatched: [string, CanonObject][] = [];
  decided: CanonObject[] = [];
  failed: [string, string][] = [];
  nodes: string[] = [];
  #idents = new Map<string, WebCryptoSigner>();

  async init(n: number, opts: { assignTimeoutS?: number; maxMigrations?: number } = {}): Promise<this> {
    this.core = await WebCryptoSigner.generate();
    this.registry = new NodeRegistry();
    this.coord = new SwarmCoordinator(this.core, this.registry, {
      dispatch: (d, m) => this.dispatched.push([d, m]),
      onDecided: (r) => this.decided.push(r),
      onFailed: (t, r) => this.failed.push([t, r]),
      ...opts,
    });
    for (let i = 0; i < n; i++) {
      const ident = await WebCryptoSigner.generate();
      this.nodes.push(ident.did);
      this.#idents.set(ident.did, ident);
      this.coord.enroll(ident.did, 0);
    }
    return this;
  }

  submit(now = 0, redundancy = 3): Promise<string> {
    return this.coord.submitCrawl('https://docs.example.com/', ['https://docs.example.com'], now, redundancy);
  }

  async respond(did: string, taskId: string, output: CanonObject, now: number): Promise<void> {
    const res = await signResult(this.#idents.get(did)!, taskId, output, Math.round(now * 1000));
    await this.coord.onResult(res, now);
  }

  assigned(): string[] {
    return this.dispatched.map(([d]) => d);
  }
}

test('quorum on honest nodes yields a core-signed record', async () => {
  const s = await new Swarm().init(3);
  const tid = await s.submit();
  const picks = s.assigned();
  assert.equal(picks.length, 3);
  await s.respond(picks[0]!, tid, HONEST, 1);
  assert.equal(s.coord.stateOf(tid), 'pending');
  await s.respond(picks[1]!, tid, HONEST, 1);
  assert.equal(s.coord.stateOf(tid), DECIDED);
  const rec = s.decided[0]!;
  assert.ok((await verifyPayload(rec)).signer === s.core.did);
  assert.equal(rec['digest'], await outputDigest(HONEST));
  assert.deepEqual(rec['nodes'], [picks[0], picks[1]].sort());
});

test('an outlier is dropped and penalized', async () => {
  const s = await new Swarm().init(3);
  const tid = await s.submit();
  const picks = s.assigned();
  await s.respond(picks[0]!, tid, OUTLIER, 1);
  await s.respond(picks[1]!, tid, HONEST, 1);
  await s.respond(picks[2]!, tid, HONEST, 1);
  assert.equal(s.coord.stateOf(tid), DECIDED);
  assert.equal(s.decided[0]!['digest'], await outputDigest(HONEST));
  assert.ok((s.registry.get(picks[0]!)?.errorEwma ?? 0) > 0);
});

test('a silent node is migrated and the task still decides', async () => {
  const s = await new Swarm().init(4, { assignTimeoutS: 5 });
  const tid = await s.submit();
  const picks = s.assigned();
  assert.equal(picks.length, 3);
  const spare = s.nodes.find((d) => !picks.includes(d))!;
  await s.respond(picks[0]!, tid, HONEST, 0); // one honest vote
  s.coord.tick(10); // picks[1], picks[2] time out -> migrate
  assert.ok(s.assigned().includes(spare), 'work moved to the spare');
  await s.respond(spare, tid, HONEST, 11);
  assert.equal(s.coord.stateOf(tid), DECIDED);
});

test('no reachable quorum fails honestly', async () => {
  const s = await new Swarm().init(3);
  const tid = await s.submit();
  const picks = s.assigned();
  await s.respond(picks[0]!, tid, { content_hash: 'd1' }, 1);
  await s.respond(picks[1]!, tid, { content_hash: 'd2' }, 1);
  await s.respond(picks[2]!, tid, { content_hash: 'd3' }, 1);
  assert.equal(s.coord.stateOf(tid), FAILED);
  assert.equal(s.failed[0]![0], tid);
});
