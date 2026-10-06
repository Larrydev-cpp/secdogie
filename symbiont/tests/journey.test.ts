// The end-to-end journey the specification asks for:
//
//   FlowState (queued) -> AttentionGap -> Gate 1 Socratic clarification -> Gate 2 inline signing
//
// Real pieces throughout: the Rust-WASM graph engine (verifying every delta),
// the lexical route adapter, Gate 1's machine, the attention scheduler, the
// stream and its renderer, and Ed25519 keys for the agent and the operator.
// Only the clock and the focus samples are scripted.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ProposalQueue } from '../src/attention/queue.ts';
import { AttentionScheduler } from '../src/attention/scheduler.ts';
import { type FocusSample, ManualFocusProvider } from '../src/attention/signals.ts';
import { WebCryptoSigner } from '../src/core/ed25519.ts';
import { TrustSet } from '../src/core/envelope.ts';
import { OPT } from '../src/gate1/machine.ts';
import { Gate2Issuer } from '../src/gate2/issuer.ts';
import { MemoryKeyring } from '../src/gate2/signer_flow.ts';
import { DualGatePipeline, type Planner } from '../src/gates/pipeline.ts';
import { TopologyEngine } from '../src/graph/topology.ts';
import { renderStream } from '../src/stream/render.ts';
import { ConsciousnessStream, type StreamItem } from '../src/stream/stream.ts';
import { DOCS, FORUM, PAGES, loadEngine } from './engine.ts';
import { FakeEl, fakeDoc } from './fake_dom.ts';

const T0 = 1_759_740_000_000; // ms

async function rig(opts: { probeTtlSeconds?: number } = {}) {
  const clock = { t: T0 };
  const now = () => clock.t;
  const engine = await loadEngine();
  const agent = await WebCryptoSigner.generate();
  const operator = await WebCryptoSigner.generate();
  const topo = new TopologyEngine({ graph: engine.createGraph([agent.did]), agent, clock: now });
  for (const url of [`${DOCS}/`, `${DOCS}/account/settings`, `${FORUM}/`]) {
    const scan = engine.scanRoutes({ document_url: url, allowed_origins: [DOCS, FORUM] }, PAGES[url]!);
    await topo.record({ scan, contentHash: 'cd'.repeat(32), byteLen: PAGES[url]!.length });
  }

  const planned: string[] = [];
  const planner: Planner = {
    planAction: (key, verb) => {
      planned.push(`${verb}:${key}`);
      return topo.graph.planAction(key, verb);
    },
  };
  const queue = new ProposalQueue();
  const scheduler = new AttentionScheduler({ queue, clock: now, timer: null });
  const provider = new ManualFocusProvider();
  scheduler.attach(provider);
  const stream = new ConsciousnessStream({ clock: now });
  const issuer = new Gate2Issuer({ agent, operators: new TrustSet([operator.did]), clock: () => now() / 1000 });
  const released: string[] = [];
  const pipeline = new DualGatePipeline({
    stream,
    scheduler,
    topology: () => topo.snapshot(),
    planner,
    issuer,
    keyring: new MemoryKeyring(operator),
    clock: now,
    ...(opts.probeTtlSeconds ? { probeTtlSeconds: opts.probeTtlSeconds } : {}),
    onRelease: (r) => released.push(r.action.target_name),
  });

  /** One-second samples; `cpm` > 0 is typing, 0 is a pause. */
  const feed = async (seconds: number, cpm: number, context: FocusSample['context'] = 'work') => {
    for (let i = 1; i <= seconds; i++) {
      clock.t += 1000;
      provider.push({
        at: clock.t,
        surface: 'other',
        context,
        inputEvents: cpm / 60,
        sampleMs: 1000,
        idleMs: cpm > 0 ? 100 : i * 1000,
      });
    }
    await pipeline.settled();
  };
  const items = () => stream.items();
  const of = <K extends StreamItem['kind']>(k: K) => items().filter((i): i is Extract<StreamItem, { kind: K }> => i.kind === k);
  return { clock, engine, agent, operator, topo, planned, queue, scheduler, stream, issuer, pipeline, released, feed, items, of };
}

test('FlowState (queued) -> AttentionGap -> Gate 1 clarification -> Gate 2 inline signing', async () => {
  const r = await rig();

  // ---- 1. FlowState: the person is typing fast in their editor -----------------------
  await r.feed(30, 240);
  assert.equal(r.scheduler.budget.mode, 'flow');
  await r.pipeline.submit('把旧账号删掉'); // "delete the old account" -- two sites have one
  await r.pipeline.settled();
  assert.equal(r.queue.queued.length, 1, 'the question waits');
  assert.equal(r.queue.queued[0]!.kind, 'gate1');
  assert.equal(r.of('gate1').length, 0, 'nothing interrupts the flow');
  assert.equal(r.of('held')[0]?.count, 1, 'only a quiet "held" line');
  assert.deepEqual(r.planned, [], 'no plan exists before alignment');

  // ---- 2. AttentionGap: a pause ------------------------------------------------------
  await r.feed(5, 0);
  assert.equal(r.scheduler.budget.mode, 'gap');
  const [q1] = r.of('gate1');
  assert.ok(q1, 'the question surfaces in the conversation');
  assert.equal(q1.state, 'open');
  assert.equal(q1.finding, 'target-ambiguous');
  assert.deepEqual(q1.options, [
    'docs.example.com/account/delete（POST 表单）',
    'forum.example.com/account/delete（POST 表单）',
    OPT.stop,
  ]);
  assert.equal(r.of('held').length, 0);

  // ---- 3. Gate 1: Socratic clarification ----------------------------------------------
  await r.pipeline.answer(q1.id, q1.options[0]!);
  await r.pipeline.settled();
  const q2 = r.of('gate1').find((q) => q.id !== q1.id)!;
  assert.equal(r.stream.gate1(q1.id)!.state, 'answered');
  assert.equal(q2.finding, 'intent-unproven', 'then: can it be undone?');
  assert.deepEqual(r.planned, [], 'still no plan');
  await r.pipeline.answer(q2.id, OPT.irreversible);
  await r.pipeline.settled();
  assert.equal(r.planned.length, 1, 'aligned: only now does the WASM engine plan');
  assert.match(r.planned[0]!, /^submit:[0-9a-f]{64}$/);

  // ---- 4. Gate 2: inline signing --------------------------------------------------------
  const [g2] = r.of('gate2');
  assert.ok(g2 && g2.bubble, 'the signature request is a bubble in the conversation');
  const b = g2.bubble;
  assert.equal(b.state, 'awaiting');
  assert.equal(b.challenge.risk_level, 'irreversible');
  assert.equal(b.challenge.target_action.target_name, 'https://docs.example.com/account/delete');
  assert.equal(b.challenge.target_action.text, 'confirm,csrf,reason');
  assert.equal(b.review.hashMatches, true);

  const root = new FakeEl('main');
  const render = () =>
    renderStream(fakeDoc, root, r.items(), { answer() {}, approve() {}, deny() {} }, r.pipeline.nowSeconds());
  render();
  assert.equal(root.byClass('approve')[0]!.attrs.get('disabled'), '', 'not armed the instant it appears');
  for (const e of root.walk()) assert.ok(e.tag !== 'dialog' && e.attrs.get('aria-modal') !== 'true');

  const early = await r.pipeline.approve(b.id);
  assert.equal(early.ok, false);
  await r.feed(1, 0);
  const signed = await r.pipeline.approve(b.id);
  assert.ok(signed.ok, signed.ok ? '' : signed.reason);
  await r.pipeline.settled();

  assert.deepEqual(r.released, ['https://docs.example.com/account/delete']);
  assert.equal(r.pipeline.releases[0]!.operator, r.operator.did);
  assert.equal(r.stream.gate2(b.id)!.bubble!.state, 'approved');
  const narration = r.of('narration').map((n) => n.text);
  assert.ok(narration.some((t) => t.startsWith('想清楚了')));
  assert.ok(narration.some((t) => t.startsWith('已放行')));
  assert.equal(r.items()[0]!.kind, 'ambient', 'the ambient stream stays on top');
  render();
  assert.equal(root.byClass('approve').length, 0);
});

test('an unanswered question expires during flow: no plan, no signature', async () => {
  const r = await rig({ probeTtlSeconds: 20 });
  await r.feed(30, 240);
  await r.pipeline.submit('把旧账号删掉');
  await r.feed(25, 240); // still typing past the deadline
  assert.equal(r.queue.all[0]!.state, 'expired');
  assert.deepEqual(r.planned, []);
  assert.equal(r.of('gate2').length, 0);
  assert.ok(r.of('narration').some((n) => n.text.includes('先放下了')));
});

test('removing mapped topology is pushed back with alternatives, and nothing is planned', async () => {
  const r = await rig();
  await r.feed(5, 0);
  await r.pipeline.submit('清空这些路由');
  await r.pipeline.settled();
  const [q] = r.of('gate1');
  assert.equal(q!.finding, 'graph-destructive');
  await r.pipeline.answer(q!.id, OPT.narrowScope);
  await r.pipeline.settled();
  assert.deepEqual(r.planned, []);
  assert.ok(r.of('narration').some((n) => n.text.startsWith('没有动手') && n.text.includes('白名单')));
});

test('a sensitive context veils the signature bubble and refuses approval', async () => {
  const r = await rig();
  await r.feed(5, 0);
  await r.pipeline.submit('删除 docs.example.com 上的账号');
  await r.pipeline.settled();
  const [q] = r.of('gate1');
  await r.pipeline.answer(q!.id, OPT.irreversible);
  await r.feed(2, 0);
  const [g2] = r.of('gate2');
  assert.ok(g2?.bubble);
  await r.feed(1, 0, 'sensitive');
  assert.equal(r.of('gate2')[0]!.bubble, null, 'no content while veiled');
  const res = await r.pipeline.approve(g2.id);
  assert.equal(res.ok, false);
  await r.feed(2, 0, 'work');
  assert.ok(r.of('gate2')[0]!.bubble, 'shown again afterwards');
  await r.feed(1, 0);
  assert.ok((await r.pipeline.approve(g2.id)).ok);
  await r.pipeline.settled();
  assert.equal(r.released.length, 1);
});

test('a signature that never comes is a refusal', async () => {
  const r = await rig();
  await r.feed(5, 0);
  await r.pipeline.submit('删除 docs.example.com 上的账号');
  await r.pipeline.settled();
  await r.pipeline.answer(r.of('gate1')[0]!.id, OPT.irreversible);
  await r.pipeline.settled();
  assert.equal(r.of('gate2').length, 1);
  await r.feed(125, 0);
  assert.deepEqual(r.released, []);
  assert.equal(r.of('gate2')[0]!.bubble!.state, 'expired');
  assert.ok(r.of('narration').some((n) => n.text.startsWith('没有放行')));
});

test('a read-only request needs no question and no signature', async () => {
  const r = await rig();
  await r.feed(5, 0);
  await r.pipeline.submit('打开 docs.example.com 的安装指南');
  await r.pipeline.settled();
  assert.equal(r.of('gate1').length, 0);
  assert.equal(r.of('gate2').length, 0);
  assert.equal(r.planned.length, 1);
  assert.deepEqual(r.released, ['https://docs.example.com/guide/install']);
});
