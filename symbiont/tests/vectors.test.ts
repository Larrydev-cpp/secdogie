// The cross-language golden vectors (fixtures/vectors/, generated from the
// Python reference): canonical bytes, Gate 2 tokens and dialogue envelopes
// must match byte for byte, in both directions.
//   node --test tests/   (from symbiont/)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { canonicalText, fromHex, parseLossless, sha256Hex } from '../src/core/canon.ts';
import { WebCryptoSigner } from '../src/core/ed25519.ts';
import { TrustSet, verifyPayload } from '../src/core/envelope.ts';
import { actionHash, createAuthorization, verifyAuthorization, type TargetAction } from '../src/gate2/authz.ts';
import { respond } from '../src/gate2/guard.ts';
import { PacketKind, ReplayGuard, Verdict, openEnvelope, seal, type Gate2ChallengePacket } from '../src/gate2/wire.ts';

const load = (name: string) =>
  JSON.parse(readFileSync(new URL(`../../fixtures/vectors/${name}`, import.meta.url), 'utf8'));

test('canonical bytes match Python for every case', async () => {
  const v = load('canonical.json');
  for (const c of v.cases) {
    const out = canonicalText(parseLossless(c.input));
    assert.equal(out, c.canonical, c.name);
    assert.equal(await sha256Hex(out), c.sha256, c.name);
  }
});

test('every reject case is refused with its reason', () => {
  const v = load('canonical.json');
  for (const c of v.reject) {
    assert.throws(
      () => parseLossless(c.input),
      (e: { reason?: string }) =>
        e.reason === c.reason || (c.reason === 'invalid JSON' && String(e.reason).startsWith('invalid')),
      c.name,
    );
  }
});

test('action hashes and authorization tokens match Python byte for byte', async () => {
  const g = load('gate2.json');
  for (const a of g.actions) {
    assert.equal(canonicalText(a.action), a.hash_input);
    assert.equal(await actionHash(a.action as TargetAction), a.action_hash);
  }
  const operator = await WebCryptoSigner.fromSeed(fromHex(g.operator.seed_hex));
  assert.equal(operator.did, g.operator.did);
  const action = g.actions[g.token.action_index].action as TargetAction;
  const token = await createAuthorization(operator, action, g.token.subject, {
    validFrom: Number(g.token.valid_from),
    expiresAt: Number(g.token.expires_at),
  });
  assert.equal(canonicalText(token), g.token.wire, 'TS-minted token is the Python token');

  const parsed = parseLossless(g.token.wire);
  const res = await verifyAuthorization(parsed, action, {
    operators: new TrustSet([g.operator.did]),
    subject: g.node.did,
    now: Number(g.token.valid_from) + 1,
  });
  assert.equal(res.ok, true, String(res.reason));
});

test('a challenge sealed by a Python node opens here', async () => {
  const g = load('gate2.json');
  const env = parseLossless(g.challenge.wire) as { header: { timestamp_ns: bigint } };
  assert.equal(typeof env.header.timestamp_ns, 'bigint', 'past 2**53 the timestamp stays exact');
  const replay = new ReplayGuard({ clockNs: () => env.header.timestamp_ns });
  const opened = await openEnvelope(env, { trust: new TrustSet([g.node.did]), selfDid: g.operator.did, replay });
  assert.equal(opened.ok, true, opened.ok ? '' : opened.reason);
  assert.ok(opened.ok && opened.packet.kind === PacketKind.Gate2Challenge);
  assert.equal(canonicalText(env), g.challenge.wire, 're-encodes to the signed bytes');
  // the same envelope again is a replay
  const again = await openEnvelope(env, { trust: new TrustSet([g.node.did]), selfDid: g.operator.did, replay });
  assert.equal(again.ok, false);
});

test('the operator response sealed here is the one Python seals', async () => {
  const g = load('gate2.json');
  const operator = await WebCryptoSigner.fromSeed(fromHex(g.operator.seed_hex));
  const env = parseLossless(g.challenge.wire) as { header: { timestamp_ns: bigint } };
  const replay = new ReplayGuard({ clockNs: () => env.header.timestamp_ns });
  const opened = await openEnvelope(env, { trust: new TrustSet([g.node.did]), selfDid: g.operator.did, replay });
  assert.ok(opened.ok && opened.packet.kind === PacketKind.Gate2Challenge);
  const challenge = opened.packet.packet as Gate2ChallengePacket;

  const resp = await respond(challenge, Verdict.Approve, {
    peerDid: g.node.did,
    operator,
    now: Number(g.response.guard_now),
  });
  assert.equal(canonicalText(resp.authorization), g.response.token_wire);
  const expected = parseLossless(g.response.wire) as { header: { session_id: string; seq: number; timestamp_ns: bigint } };
  const h = expected.header;
  const sealed = await seal(
    operator,
    {
      version: 'secdogie/dialogue/v1',
      sender_did: operator.did,
      recipient_did: g.node.did,
      session_id: h.session_id,
      seq: h.seq,
      timestamp_ns: h.timestamp_ns,
    },
    { kind: PacketKind.Gate2Response, packet: resp },
  );
  assert.equal(canonicalText(sealed), g.response.wire);
  assert.equal((await verifyPayload(sealed, new TrustSet([operator.did]))).ok, true);
});

test('graph delta envelopes signed by Python verify here', async () => {
  const g = load('graph_delta.json');
  const agent = await WebCryptoSigner.fromSeed(fromHex(g.agent.seed_hex));
  assert.equal(agent.did, g.agent.did);
  for (const v of g.valid) {
    const env = parseLossless(v.wire);
    assert.equal((await verifyPayload(env, new TrustSet([g.agent.did]))).ok, true, v.name);
    assert.equal(await sha256Hex(canonicalText(parseLossless(v.payload))), v.cid, v.name);
  }
});
