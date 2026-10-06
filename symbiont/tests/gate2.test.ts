// Gate 2: the agent releases a high-risk action only on a valid operator
// token, and the operator's inline bubble cannot be approved early, while
// veiled, twice, or after it expired.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { WebCryptoSigner } from '../src/core/ed25519.ts';
import { TrustSet } from '../src/core/envelope.ts';
import { targetAction } from '../src/gate2/authz.ts';
import { Gate2Issuer, type PlanPreview } from '../src/gate2/issuer.ts';
import { Gate2SignerFlow, MemoryKeyring, UserGestureKeyring } from '../src/gate2/signer_flow.ts';
import { type Gate2ResponsePacket, Verdict } from '../src/gate2/wire.ts';

const preview = (risk: PlanPreview['risk']): PlanPreview => ({
  target_action: targetAction({
    kind: 'submit',
    target_id: 'f'.repeat(64),
    target_role: 'form',
    target_name: 'https://docs.example.com/account/delete',
    text: 'confirm,reason',
    high_risk: risk !== 'low',
  }),
  risk,
  mutating: risk !== 'low',
  method: risk === 'low' ? 'get' : 'post',
  fields: ['confirm', 'reason'],
  origin: 'https://docs.example.com',
  route: '/account/delete',
});

async function rig() {
  const clock = { t: 1_759_740_000 };
  const agent = await WebCryptoSigner.generate();
  const operator = await WebCryptoSigner.generate();
  const issuer = new Gate2Issuer({ agent, operators: new TrustSet([operator.did]), clock: () => clock.t });
  const sent: Gate2ResponsePacket[] = [];
  const flow = new Gate2SignerFlow({
    peerDid: agent.did,
    keyring: new MemoryKeyring(operator),
    send: (r) => void sent.push(r),
    clock: () => clock.t,
  });
  return { clock, agent, operator, issuer, flow, sent };
}

test('a low-risk action passes without a challenge', async () => {
  const { issuer } = await rig();
  const r = await issuer.gate(preview('low'), '');
  assert.equal(r.kind, 'released');
  assert.ok(r.kind === 'released' && r.release.token === null);
});

test('approve -> token -> release, and only once', async () => {
  const { clock, issuer, flow, sent, operator } = await rig();
  const r = await issuer.gate(preview('irreversible'), 'deletes the account');
  assert.ok(r.kind === 'challenge');
  assert.equal(r.packet.risk_level, 'irreversible');

  const b = await flow.present(r.packet);
  assert.equal(b.state, 'awaiting');
  assert.equal(b.review.hashMatches, true);

  const early = await flow.approve(b.id);
  assert.equal(early.ok, false, 'not armed yet');
  clock.t += 1;
  const ok = await flow.approve(b.id);
  assert.ok(ok.ok);
  assert.equal(flow.bubble(b.id)!.state, 'approved');
  assert.equal(sent.length, 1);

  const settled = await issuer.receive(sent[0]!);
  assert.equal(settled.kind, 'released');
  assert.ok(settled.kind === 'released' && settled.release.operator === operator.did);

  const replay = await issuer.receive(sent[0]!);
  assert.equal(replay.kind, 'refused', 'one response per challenge');
  assert.equal((await flow.approve(b.id)).ok, false, 'no double signing');
});

test('deny, expiry and a stranger operator all refuse', async () => {
  const { clock, issuer, flow, sent, agent } = await rig();
  const c1 = await issuer.gate(preview('high'), 'x');
  assert.ok(c1.kind === 'challenge');
  const b1 = await flow.present(c1.packet);
  await flow.deny(b1.id);
  assert.equal((await issuer.receive(sent[0]!)).kind, 'refused');

  const c2 = await issuer.gate(preview('high'), 'x');
  assert.ok(c2.kind === 'challenge');
  await flow.present(c2.packet);
  clock.t += 121;
  assert.equal(flow.expire().length, 1);
  assert.deepEqual(issuer.expire(), [c2.packet.challenge_id]);

  // an operator the agent does not trust signs a perfectly good token
  const stranger = await WebCryptoSigner.generate();
  const c3 = await issuer.gate(preview('high'), 'x');
  assert.ok(c3.kind === 'challenge');
  const out: Gate2ResponsePacket[] = [];
  const other = new Gate2SignerFlow({
    peerDid: agent.did,
    keyring: new MemoryKeyring(stranger),
    send: (r) => void out.push(r),
    clock: () => clock.t,
    armingDelaySeconds: 0,
  });
  await other.present(c3.packet);
  assert.ok((await other.approve(c3.packet.challenge_id)).ok);
  const s = await issuer.receive(out[0]!);
  assert.ok(s.kind === 'refused' && /not trusted/.test(s.reason));
});

test('a node cannot show one action and collect a signature for another', async () => {
  const { issuer, flow } = await rig();
  const c = await issuer.gate(preview('high'), 'x');
  assert.ok(c.kind === 'challenge');
  const forged = { ...c.packet, target_action: { ...c.packet.target_action, target_name: 'https://docs.example.com/harmless' } };
  const b = await flow.present(forged);
  assert.equal(b.state, 'refused');
  assert.match(b.note, /action_hash/);
});

test('veiled bubbles cannot be approved and re-arm when shown again', async () => {
  const { clock, issuer, flow } = await rig();
  const c = await issuer.gate(preview('high'), 'x');
  assert.ok(c.kind === 'challenge');
  const b = await flow.present(c.packet);
  clock.t += 1;
  flow.veil(b.id);
  assert.equal((await flow.approve(b.id)).ok, false);
  flow.unveil(b.id);
  assert.equal((await flow.approve(b.id)).ok, false, 're-armed');
  clock.t += 1;
  assert.equal((await flow.approve(b.id)).ok, true);
});

test('the gesture keyring fails closed without a user activation', async () => {
  const k = new UserGestureKeyring(await WebCryptoSigner.generate());
  await assert.rejects(k.unlock(), /your own click/);
  assert.equal(Verdict.Approve, 'Approve');
});
