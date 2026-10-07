// What reaches the page. Machinery never does: sentinel values planted in
// every mechanical field (action hash, challenge id, nonce, signature, DIDs,
// element ids) must not appear anywhere in the rendered text or attributes --
// while the content a signature covers appears whole, hidden characters made
// visible. And the cards behave: two buttons on Gate 2, Approve only once
// armed, nothing re-created under the pointer.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { Turn } from '../src/client/core_link.ts';
import type { TargetAction } from '../src/gate2/authz.ts';
import { DialogueType, dialoguePacket } from '../src/gate2/wire.ts';
import { ThreadView } from '../src/ui/render.ts';
import { ambientLine, presenceHint } from '../src/ui/presence.ts';
import { consentView } from '../src/voice/consent.ts';
import { askView } from '../src/voice/question.ts';
import { en, zh } from '../src/voice/locale.ts';
import { FakeEl, fakeDoc } from './fake_dom.ts';

const S = {
  hash: 'f00dfacef00dfacef00dfacef00dfacef00dfacef00dfacef00dfacef00dface',
  challenge: 'c4a11e9e5e771ne1',
  probe: 'pr0be5e771ne1abc',
  nonce: 'n0nce5e771ne1xyz',
  node: 'did:key:z6MkSENTINELnode00000000000000000000000000000',
  app: 'did:key:z6MkSENTINELapp000000000000000000000000000000',
  element: 'element:9876543210',
  sig: 'c2lnbmF0dXJlLXNlbnRpbmVs',
};

// consent content a person must see whole: a 64-hex run, a did:key, a newline, a bidi override, a ZWJ
const CONTENT = `pay ${'ab'.repeat(32)} to did:key:z6MkPAYEE\nnote: ‮exe.txt‍`;

function turns(voice = zh): Turn[] {
  const action: TargetAction = { kind: 'type', target_id: S.element, target_role: 'textbox', target_name: 'Wallet', text: CONTENT, high_risk: true };
  const c = consentView(voice, action);
  const a = askView(voice, dialoguePacket({
    probe_id: S.probe, dialogue_type: DialogueType.SocraticQuestion, content: 'Execute HIGH-RISK key(delete)?',
    suggested_options: ['Approve', 'Deny'], gate_finding: 'destructive-chain',
  }));
  return [
    { kind: 'you', id: 't1', rev: 1, text: '把旧账号注销掉' },
    { kind: 'say', id: 't2', rev: 1, text: voice.reply.accepted, tone: 'plain' },
    { kind: 'ask', id: 't3', rev: 1, ...a, state: 'open', answer: null },
    { kind: 'consent', id: 't4', rev: 1, ...c, state: 'awaiting', armedAt: 100.8, canApprove: true },
    { kind: 'pairing', id: 't5', rev: 1, stage: 'confirm', code: '8779 5389 3074' },
  ];
}

function draw(ts: Turn[], now = 100, voice = zh) {
  const root = new FakeEl('ol');
  const calls: string[] = [];
  const view = new ThreadView(fakeDoc, root, voice, {
    answer: (id, v) => calls.push(`answer ${id} ${v}`),
    approve: (id) => calls.push(`approve ${id}`),
    cancel: (id) => calls.push(`cancel ${id}`),
    confirmPairing: () => calls.push('pair'),
    cancelPairing: () => calls.push('unpair'),
  });
  const next = view.render(ts, now);
  return { root, calls, view, next };
}

test('no machinery on the page: no sentinel in any text or attribute', () => {
  for (const voice of [zh, en]) {
    const { root } = draw(turns(voice), 100, voice);
    const all = root.everything();
    for (const [name, value] of Object.entries(S)) assert.ok(!all.includes(value), `${name} leaked`);
    assert.doesNotMatch(all, /element:|challenge|action_hash|risk_explanation/);
  }
});

test('signed content appears whole, with hidden characters visible', () => {
  const { root } = draw(turns());
  const quote = root.byClass('quote')[0]!.textContent!;
  assert.ok(quote.includes('ab'.repeat(32)), 'the 64-hex run is shown, not scrubbed');
  assert.ok(quote.includes('did:key:z6MkPAYEE'), 'the did:key in the text is shown');
  assert.ok(quote.includes('⏎') && quote.includes('⟨U+202E⟩') && quote.includes('⟨U+200D⟩'));
});

test('Gate 2 has exactly Cancel and Approve, and Approve arms after a moment', () => {
  const { root, calls, view, next } = draw(turns(), 100);
  const card = root.byClass('consent')[0]!;
  const buttons = [...card.walk()].filter((e) => e.tag === 'button');
  assert.deepEqual(buttons.map((b) => b.textContent), ['取消', '批准']);
  const approve = card.byClass('approve')[0]!;
  assert.ok(approve.attrs.has('disabled'));
  assert.equal(next, 100.8);
  approve.click();
  assert.deepEqual(calls, []);
  view.arm(101);
  assert.ok(!approve.attrs.has('disabled'));
  approve.click();
  card.byClass('cancel')[0]!.click();
  assert.deepEqual(calls, ['approve t4', 'cancel t4']);
});

test('a browser without the operator key gets a disabled Approve and the honest reason', () => {
  const ts = turns().map((t) => (t.kind === 'consent' ? { ...t, canApprove: false } : t));
  const { root, view } = draw(ts, 200);
  view.arm(300);
  const card = root.byClass('consent')[0]!;
  assert.ok(card.byClass('approve')[0]!.attrs.has('disabled'));
  assert.ok(card.textContent!.includes(zh.consent.cannotApprove));
});

test('Gate 1 chips send the exact value; the gentle words are what shows', () => {
  const { root, calls } = draw(turns());
  const ask = root.byClass('ask')[0]!;
  assert.ok(ask.textContent!.startsWith(zh.ask.finding['destructive-chain']));
  const chips = ask.byClass('chip');
  assert.deepEqual(chips.map((c) => c.textContent), ['可以', '先不要']);
  chips[0]!.click();
  assert.deepEqual(calls, ['answer t3 Approve']);
});

test('a card that did not change is not re-created', () => {
  const ts = turns();
  const { root, view } = draw(ts);
  const before = root.byClass('consent')[0];
  view.render([...ts, { kind: 'say', id: 't6', rev: 1, text: '做完了。', tone: 'done' }], 100.5);
  assert.equal(root.byClass('consent')[0], before);
  view.render(ts.map((t) => (t.id === 't4' ? { ...t, rev: 2, state: 'sent' } : t)) as Turn[], 101);
  assert.notEqual(root.byClass('consent')[0], before);
  assert.ok(root.byClass('consent')[0]!.textContent!.includes(zh.consent.sent));
});

test('the pairing card shows the code and asks for one tap', () => {
  const { root, calls } = draw(turns());
  const card = root.byClass('pairing')[0]!;
  assert.ok(card.textContent!.includes('8779 5389 3074'));
  card.byClass('approve')[0]!.click();
  assert.deepEqual(calls, ['pair']);
});

test('the ambient line and the tooltip say nothing mechanical', () => {
  assert.equal(ambientLine(zh, { phase: 'connected', reason: null }, { running: false, waiting: false }, true), zh.ambient.idle);
  assert.equal(ambientLine(zh, { phase: 'connected', reason: null }, { running: true, waiting: true }, false), zh.ambient.waiting);
  assert.equal(ambientLine(zh, { phase: 'unpaired', reason: 'not-enrolled' }, { running: false, waiting: false }, true), zh.why.notEnrolled);
  assert.equal(presenceHint(zh, { phase: 'busy', reason: 'another-tab' }), zh.why.anotherTab);
  assert.equal(presenceHint(en, { phase: 'connected', reason: null }), en.presenceHint.connected);
});
