// The stream: never a blank slate, never a step log, never a modal.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { targetAction } from '../src/gate2/authz.ts';
import type { Gate2Bubble } from '../src/gate2/signer_flow.ts';
import { RiskLevel } from '../src/gate2/wire.ts';
import { ConsciousnessStream } from '../src/stream/stream.ts';
import { renderStream } from '../src/stream/render.ts';
import { FakeEl, fakeDoc } from './fake_dom.ts';

test('idle is the ambient state of the swarm and topology, not an empty greeting', () => {
  const s = new ConsciousnessStream({ clock: () => 100_000 });
  s.setAmbient({ peers: 3, heads: 2, deltas: 41, lastSyncAt: 88_000 }, { origins: 2, states: 37, references: 52, recentStates: 14 }, 'gap');
  const items = s.items();
  assert.equal(items.length, 1);
  assert.equal(items[0]!.kind, 'ambient');
  const root = new FakeEl('main');
  renderStream(fakeDoc, root, items, { answer() {}, approve() {}, deny() {} }, 0);
  const text = root.textContent ?? '';
  assert.match(text, /3 个节点/);
  assert.match(text, /37 个状态/);
  assert.match(text, /12 秒前同步过/);
  assert.doesNotMatch(text, /今天|what should i do/i);
});

test('fourteen low-level scan events read as one line of intent', () => {
  const s = new ConsciousnessStream();
  for (let i = 0; i < 14; i++) s.narrate({ type: 'mapped', origin: 'https://docs.example.com', states: 1, at: 1000 + i * 500 });
  s.narrate({ type: 'synced', peer: 'a', deltas: 3, at: 9000 });
  s.narrate({ type: 'synced', peer: 'b', deltas: 4, at: 9500 });
  const lines = s.items().filter((i) => i.kind === 'narration');
  assert.equal(lines.length, 2);
  assert.ok(lines[0]!.kind === 'narration' && lines[0]!.text === '在 docs.example.com 梳理出 14 个候选状态（看了 14 个页面）');
  assert.ok(lines[1]!.kind === 'narration' && lines[1]!.text === '和 2 个节点交换了 7 条增量');
});

const bubble = (armedAt: number): Gate2Bubble => ({
  id: 'c1',
  challenge: {
    challenge_id: 'c1',
    target_action: targetAction({
      kind: 'submit',
      target_id: 'ab'.repeat(32),
      target_role: 'form',
      target_name: 'https://docs.example.com/account/delete',
      text: 'confirm,reason',
      high_risk: true,
    }),
    risk_level: RiskLevel.Irreversible,
    risk_explanation: '<img src=x onerror=alert(1)> 删除账号',
    action_hash: 'f'.repeat(64),
    subject_did: 'did:key:z6Mk',
    expires_at: 1_759_740_120,
  },
  review: {
    challenge: null as never,
    localHash: 'f'.repeat(64),
    problems: [],
    hashMatches: true,
    signable: true,
  },
  state: 'awaiting',
  surfacedAt: 0,
  armedAt,
  veiled: false,
  note: '',
});

test('a Gate 2 bubble is a turn in the conversation, with everything it signs', () => {
  const s = new ConsciousnessStream();
  s.putGate2({ id: 'c1', at: 5, veiled: false, bubble: bubble(10) });
  const root = new FakeEl('main');
  const calls: string[] = [];
  const h = { answer() {}, approve: (id: string) => calls.push(`approve:${id}`), deny: (id: string) => calls.push(`deny:${id}`) };
  renderStream(fakeDoc, root, s.items(), h, 5);

  for (const e of root.walk()) {
    assert.notEqual(e.attrs.get('role'), 'dialog');
    assert.notEqual(e.attrs.get('aria-modal'), 'true');
    assert.notEqual(e.tag, 'dialog');
  }
  const art = root.find((e) => e.tag === 'article')!;
  assert.match(art.className, /turn bubble gate2|turn turn-agent bubble gate2/);
  const text = art.textContent ?? '';
  for (const s of ['https://docs.example.com/account/delete', 'confirm,reason', 'form', 'submit', '不可撤回', '有效至']) {
    assert.ok(text.includes(s), s);
  }
  // node-supplied text is text, not markup
  assert.ok(text.includes('<img src=x onerror=alert(1)>'));

  const approve = root.byClass('approve')[0]!;
  assert.equal(approve.attrs.get('disabled'), '', 'not armed at t=5');
  renderStream(fakeDoc, root, s.items(), h, 11);
  root.byClass('approve')[0]!.click();
  root.byClass('deny')[0]!.click();
  assert.deepEqual(calls, ['approve:c1', 'deny:c1']);
});

test('a veiled bubble carries nothing in the view model or the page', () => {
  const s = new ConsciousnessStream();
  s.putGate2({ id: 'c1', at: 5, veiled: true, bubble: bubble(0) });
  s.putGate1({ id: 'p1', at: 6, veiled: true, state: 'open', question: '你指的是哪一个？', finding: 'target-ambiguous', options: ['a', 'b'], answer: null });
  s.setHeld(2);
  const items = s.items();
  const g2 = items.find((i) => i.kind === 'gate2')!;
  assert.ok(g2.kind === 'gate2' && g2.bubble === null);
  const g1 = items.find((i) => i.kind === 'gate1')!;
  assert.ok(g1.kind === 'gate1' && g1.question === '' && g1.options.length === 0);
  const root = new FakeEl('main');
  renderStream(fakeDoc, root, items, { answer() {}, approve() {}, deny() {} }, 100);
  const text = root.textContent ?? '';
  assert.ok(!text.includes('account/delete') && !text.includes('哪一个'));
  assert.match(text, /还有 2 件事/);
  assert.equal(root.byClass('approve').length, 0);
});

test('a Gate 1 question answers by option or in free text', () => {
  const s = new ConsciousnessStream();
  s.putGate1({ id: 'p1', at: 1, veiled: false, state: 'open', question: '有 2 个地方都对得上。你指的是哪一个？', finding: 'target-ambiguous', options: ['docs', 'forum'], answer: null });
  const root = new FakeEl('main');
  const got: string[] = [];
  renderStream(fakeDoc, root, s.items(), { answer: (id, t) => got.push(`${id}=${t}`), approve() {}, deny() {} }, 0);
  assert.match(root.textContent ?? '', /目标不明确/);
  root.byClass('option')[1]!.click();
  const input = root.find((e) => e.tag === 'input')!;
  input.value = '  第一个  ';
  root.byClass('send')[0]!.click();
  assert.deepEqual(got, ['p1=forum', 'p1=第一个']);
});
