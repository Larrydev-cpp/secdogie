// Gate 1: the wording rules agree with Python's socratic.review, and the
// alignment machine questions, pushes back and declines instead of guessing.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { type TopologyTarget } from '../src/gate1/interpret.ts';
import { ProbeLedger } from '../src/gate1/ledger.ts';
import { Gate1Machine, OPT, isAligned } from '../src/gate1/machine.ts';
import { reviewWording } from '../src/gate1/rules.ts';
import { DialogueType, dialoguePacket } from '../src/gate2/wire.ts';

test('wording review matches socratic.review on every vector', () => {
  const v = JSON.parse(readFileSync(new URL('../../fixtures/vectors/socratic.json', import.meta.url), 'utf8'));
  for (const c of v.cases) {
    const r = reviewWording(c.instruction);
    assert.equal(r.verdict, c.verdict, JSON.stringify(c.instruction));
    assert.deepEqual([...r.reasons], c.reasons, JSON.stringify(c.instruction));
    assert.equal(r.suggestion, c.suggestion, JSON.stringify(c.instruction));
  }
});

const target = (origin: string, route: string, form: TopologyTarget['form'] = null): TopologyTarget => ({
  stateKey: `${origin}${route}`.padEnd(64, '0').slice(0, 64),
  origin,
  route,
  queryKeys: [],
  form,
});

const TOPO = {
  targets: [
    target('https://docs.example.com', '/account/delete', { method: 'post', fields: ['confirm', 'reason'] }),
    target('https://forum.example.com', '/account/delete', { method: 'post', fields: ['confirm'] }),
    target('https://docs.example.com', '/guide/install'),
    target('https://docs.example.com', '/search', { method: 'get', fields: ['q'] }),
  ],
};

function setup(instruction: string, now = { t: 1000 }) {
  let n = 0;
  const ledger = new ProbeLedger({ clock: () => now.t, idFactory: () => `p${++n}`, ttlSeconds: 60 });
  const m = new Gate1Machine({ id: 'i1', instruction, topology: () => TOPO, ledger });
  const reply = (probeId: string, content: string) =>
    dialoguePacket({ probe_id: `a-${probeId}`, dialogue_type: DialogueType.UserClarification, content, in_reply_to: probeId });
  return { m, ledger, reply, now };
}

test('an ambiguous destructive intent is questioned twice, then aligned', () => {
  const { m, reply } = setup('把旧账号删掉');
  const s1 = m.start();
  assert.equal(s1.kind, 'probe');
  assert.ok(s1.kind === 'probe');
  assert.equal(s1.finding.code, 'target-ambiguous');
  assert.equal(s1.packet.dialogue_type, DialogueType.SocraticQuestion);
  assert.deepEqual(s1.packet.suggested_options, [
    'docs.example.com/account/delete（POST 表单）',
    'forum.example.com/account/delete（POST 表单）',
    OPT.stop,
  ]);
  assert.equal(m.state, 'probing');

  const s2 = m.answer(reply(s1.probe.probeId, 'docs.example.com/account/delete（POST 表单）'), 'did:op');
  assert.ok(s2.kind === 'probe');
  assert.equal(s2.finding.code, 'intent-unproven');

  const s3 = m.answer(reply(s2.probe.probeId, OPT.irreversible), 'did:op');
  assert.ok(s3.kind === 'aligned');
  assert.equal(m.state, 'aligned');
  assert.equal(s3.intent.target.origin, 'https://docs.example.com');
  assert.equal(s3.intent.contract.irreversible, true);
  assert.equal(s3.intent.destructive, true);
  assert.equal(s3.intent.clarifications.length, 2);
  assert.ok(isAligned(s3.intent));
  assert.ok(!isAligned({ ...s3.intent }), 'a copy is not an issued alignment');
});

test('a free-text rollback answers the irreversibility question', () => {
  const { m, reply } = setup('删除 docs.example.com 上的账号');
  const s1 = m.start();
  assert.ok(s1.kind === 'probe' && s1.finding.code === 'intent-unproven');
  const s2 = m.answer(reply(s1.probe.probeId, '30 天内可以在邮件里恢复'), 'did:op');
  assert.ok(s2.kind === 'aligned');
  assert.equal(s2.intent.contract.rollback, '30 天内可以在邮件里恢复');
});

test('a target the graph does not hold is never invented', () => {
  const { m } = setup('打开价格页面');
  const s = m.start();
  assert.ok(s.kind === 'probe' && s.finding.code === 'target-unproven');
  // the options are the graph's own states, and a way out
  assert.ok(s.packet.suggested_options.every((o) => o === OPT.stop || o.includes('example.com')));
});

test('removing mapped state is pushed back, with alternatives', () => {
  const { m, reply } = setup('forget the routes on docs.example.com');
  const s1 = m.start();
  assert.ok(s1.kind === 'probe' && s1.finding.code === 'graph-destructive');
  const s2 = m.answer(reply(s1.probe.probeId, OPT.narrowScope), 'did:op');
  assert.ok(s2.kind === 'declined');
  assert.ok(s2.alternatives.length > 0);
  assert.equal(m.state, 'declined');
});

test('a read-only request aligns without a single question', () => {
  const { m } = setup('打开 docs.example.com 的安装指南');
  const s = m.start();
  assert.ok(s.kind === 'aligned');
  assert.equal(s.intent.verb, 'navigate');
  assert.equal(s.intent.target.route, '/guide/install');
  assert.equal(s.intent.mutating, false);
});

test('an unanswered question expires, and a late answer revives nothing', () => {
  const { m, reply, now } = setup('把旧账号删掉');
  const s1 = m.start();
  assert.ok(s1.kind === 'probe');
  now.t += 61;
  const e = m.expire();
  assert.ok(e && e.kind === 'expired');
  assert.equal(m.state, 'expired');
  const late = m.answer(reply(s1.probe.probeId, OPT.irreversible), 'did:op');
  assert.equal(late.kind, 'ignored');
});

test('after maxRounds without alignment the machine declines', () => {
  const { m, reply } = setup('把旧账号删掉');
  let s = m.start();
  for (let i = 0; i < 3 && s.kind === 'probe'; i++) {
    s = m.answer(reply(s.probe.probeId, '不知道'), 'did:op'); // never narrows it down
  }
  assert.equal(s.kind, 'declined');
});

test('wording findings come first: unattended posting is questioned', () => {
  const { m, reply } = setup('automatically post a reply on my behalf');
  const s1 = m.start();
  assert.ok(s1.kind === 'probe' && s1.finding.code === 'unattended-posting');
  const s2 = m.answer(reply(s1.probe.probeId, OPT.stop), 'did:op');
  assert.ok(s2.kind === 'declined');
});
