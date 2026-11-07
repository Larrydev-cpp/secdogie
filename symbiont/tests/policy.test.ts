// Policy bundles against the Python reference (fixtures/vectors/policy.json):
// every case gets the same verdict -- ok, the full sorted error list, the
// signers that counted -- every phrase the same normal form, every chain the
// same errors, and the floor is the same table.
//   node --test tests/   (from symbiont/)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { type CanonObject, parseLossless } from '../src/core/canon.ts';
import { TrustSet } from '../src/core/envelope.ts';
import {
  FLOOR,
  checkBundle,
  checkSuccessor,
  containsPhrase,
  normalizePhrase,
  verifyBundle,
} from '../src/policy/bundle.ts';

const v = JSON.parse(readFileSync(new URL('../../fixtures/vectors/policy.json', import.meta.url), 'utf8'));
const trust = {
  operators: new TrustSet([v.operator.did]),
  masters: { dids: new TrustSet(v.masters.map((m: { did: string }) => m.did)), threshold: v.threshold },
};

test('the floor is the Python floor', () => {
  assert.deepEqual({ ...FLOOR }, v.floor);
});

test('every bundle gets the Python verdict', async () => {
  assert.ok(v.cases.length > 60);
  for (const c of v.cases) {
    const got = await verifyBundle(parseLossless(c.bundle), trust);
    assert.deepEqual(
      { ok: got.ok, errors: [...got.errors], signers: [...got.signers] },
      c.expect,
      c.name,
    );
  }
});

test('phrases normalize as in Python', () => {
  for (const n of v.normalize) assert.equal(normalizePhrase(n.input), n.normalized, JSON.stringify(n.input));
  assert.ok(containsPhrase('Please MASS   Register 50 accounts', 'mass register'));
  assert.ok(!containsPhrase('anything', '   '));
});

test('version chains break where Python says', () => {
  for (const c of v.chain) {
    const current = c.current === null ? null : (parseLossless(c.current) as CanonObject);
    assert.deepEqual(checkSuccessor(current, parseLossless(c.candidate) as CanonObject), c.errors, c.name);
  }
});

test('no trust set authorizes nothing', async () => {
  const ok = v.cases.find((c: { name: string }) => c.name === 'gate-rules signed by an operator');
  const dm = v.cases.find((c: { name: string }) => c.name === 'decision-model signed by 2 of 3 masters');
  assert.deepEqual((await verifyBundle(parseLossless(ok.bundle))).errors, ['unauthorized:sigs']);
  assert.deepEqual((await verifyBundle(parseLossless(dm.bundle), { operators: trust.operators })).errors, [
    'unauthorized:sigs',
  ]);
  assert.deepEqual(await checkBundle(parseLossless(ok.bundle)), []);
});
