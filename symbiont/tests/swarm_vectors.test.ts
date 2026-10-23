// The swarm wire/task/result contract against swarm/fixtures/swarm_vectors.json
// (the Python reference). Every wire message encodes to the same bytes, the
// core-signed task and node-signed result verify, output digests recompute, and
// -- re-deriving the node's test key from the same seed -- this TypeScript node
// reproduces Python's result signature byte for byte.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { type CanonObject, type CanonValue, canonicalText, parseLossless } from '../src/core/canon.ts';
import { WebCryptoSigner } from '../src/core/ed25519.ts';
import { payloadOf, verifyPayload } from '../src/core/envelope.ts';
import { outputDigest, signResult } from '../src/swarm/tasks.ts';
import * as wire from '../src/swarm/wire.ts';

const V = JSON.parse(readFileSync(new URL('../../swarm/fixtures/swarm_vectors.json', import.meta.url), 'utf8'));

/** A plain parsed-JSON value as a CanonValue (ints are safe numbers here). */
const canon = (obj: unknown): CanonValue => parseLossless(JSON.stringify(obj));
const canonObj = (obj: unknown): CanonObject => canon(obj) as CanonObject;

async function seedFor(label: string): Promise<Uint8Array> {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`secdogie/swarm-vectors/${label}`));
  return new Uint8Array(d);
}

test('every wire message encodes byte-identically and decodes back', () => {
  for (const [name, c] of Object.entries<Record<string, unknown>>(V.wire)) {
    // the message object canonicalizes to exactly the stored bytes
    assert.equal(canonicalText(canon(c['message'])), c['canonical'], `${name}: encode`);
    // and decode of those bytes round-trips to the same canonical bytes
    assert.equal(canonicalText(wire.decode(c['canonical'] as string)), c['canonical'], `${name}: decode`);
  }
});

test('wire.decode is strict', () => {
  const good = new TextDecoder().decode(wire.encode(wire.heartbeat('did:key:z6MkTest')));
  assert.throws(() => wire.decode(good.replace(wire.WIRE_PROTOCOL, 'secdogie/swarm-wire/v9')), wire.WireError);
  assert.throws(() => wire.decode('{"v":"secdogie/swarm-wire/v1","type":"NOPE"}'), wire.WireError);
  assert.throws(() => wire.decode('{"v":"secdogie/swarm-wire/v1","type":"HEARTBEAT"}'), wire.WireError);
  assert.throws(() => wire.decode('{"v":"secdogie/swarm-wire/v1","type":"PROBE","nonce":"n","sent_at_ms":NaN}'), wire.WireError);
  assert.throws(() => wire.decode('{"v":"secdogie/swarm-wire/v1","v":"x","type":"HEARTBEAT","node_did":"d"}'), wire.WireError);
});

test('the core-signed task verifies and its payload canonical matches', async () => {
  const signed = canonObj(V.task.signed);
  const v = await verifyPayload(signed);
  assert.ok(v.ok, 'task signature valid');
  assert.equal(v.signer, V.identities.core);
  assert.equal(canonicalText(payloadOf(signed)), V.task.payload_canonical);
});

test('the node-signed result verifies and digests recompute', async () => {
  const signed = canonObj(V.result.signed);
  const v = await verifyPayload(signed);
  assert.ok(v.ok && v.signer === V.identities.node);
  assert.equal(await outputDigest(canonObj(V.result.signed.output)), V.result.output_digest);
  for (const c of V.digests) {
    assert.equal(await outputDigest(canonObj(c.output)), c.digest);
  }
});

test('this TS node reproduces the Python result signature byte for byte', async () => {
  const node = await WebCryptoSigner.fromSeed(await seedFor('node'));
  assert.equal(node.did, V.identities.node, 'same seed -> same DID across languages');
  const produced = await signResult(
    node,
    V.result.signed.task_id as string,
    canonObj(V.result.signed.output),
    V.result.signed.produced_at_ms as number,
  );
  assert.equal(canonicalText(produced), canonicalText(canonObj(V.result.signed)), 'identical signed bytes (Ed25519 is deterministic)');
});
