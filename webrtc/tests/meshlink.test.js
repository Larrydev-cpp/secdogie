// The browser's mesh code against the Python reference: rooms, roster, hellos
// and a Python-signed frame (fixtures/vectors/meshlink.json) must come out the
// same here, byte for byte where signatures are involved.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import {
  ReplayWindow, decodeFrame, encodeFrame, muxDecode, muxEncode, newCounter, parseJson,
} from '../client/frames.js';
import { Roster, createHello, linkRoom, verifyHello } from '../client/meshlink.js';
import { base64ToBytes, createAdmission, generateIdentity, identityFromSeed } from '../client/pairing.js';

const V = JSON.parse(readFileSync(new URL('../../fixtures/vectors/meshlink.json', import.meta.url), 'utf8'));
const owner = await identityFromSeed(V.keys.owner.seed_hex);
const phone = await identityFromSeed(V.keys.phone.seed_hex);
const tablet = await identityFromSeed(V.keys.tablet.seed_hex);

async function roster(...admissions) {
  const r = new Roster(V.mesh, [owner.did]);
  for (const a of admissions) await r.admit(a);
  return r;
}

test('the same keys, and both ends of every pair meet in the same room as in Python', async () => {
  assert.deepEqual([owner.did, phone.did, tablet.did], [V.keys.owner.did, V.keys.phone.did, V.keys.tablet.did]);
  assert.equal(await linkRoom(V.mesh, owner.did, phone.did), V.rooms.owner_phone);
  assert.equal(await linkRoom(V.mesh, phone.did, owner.did), V.rooms.owner_phone);
  assert.equal(await linkRoom(V.mesh, tablet.did, phone.did), V.rooms.phone_tablet);
  await assert.rejects(linkRoom(V.mesh, phone.did, phone.did));
});

test('the roster holds, trades and names itself exactly as in Python', async () => {
  const full = await roster(V.admissions.phone, V.admissions.tablet);
  assert.deepEqual(full.devices(), V.roster.devices);
  assert.deepEqual(full.wire(), V.roster.wire);
  assert.equal(await full.digest(), V.roster.digest);
  const atPhone = await roster(V.admissions.phone);
  assert.equal(await atPhone.digest(), V.roster.phone_only_digest);
  assert.equal(await atPhone.merge(V.roster.wire), 1);
  assert.equal(await atPhone.digest(), V.roster.digest);
  assert.equal(atPhone.name(tablet.did), '平板');
  // nothing but an owner's signature adds a device
  const stranger = await generateIdentity();
  const foreign = await createAdmission(stranger, V.mesh, tablet.did, { inviteId: '0'.repeat(32) });
  assert.equal(await (await roster()).admit(foreign), false);
  assert.equal(await atPhone.merge({ ...V.roster.wire, mesh: 'office' }), 0);
});

test('each end of a link: Python-signed hellos verify here, and the same inputs sign the same bytes', async () => {
  const full = await roster(V.admissions.phone, V.admissions.tablet);
  const atPhone = await roster(V.admissions.phone);
  const p = V.hellos.phone.args;
  const o = V.hellos.owner.args;
  assert.deepEqual(await createHello(phone, {
    mesh: V.mesh, nonce: p.nonce, localFp: p.local_fp, remoteFp: p.remote_fp, admission: V.admissions.phone,
    name: p.name, roster: p.roster, now: p.clock,
  }), V.hellos.phone.signed);
  assert.deepEqual(await createHello(owner, {
    mesh: V.mesh, nonce: o.nonce, localFp: o.local_fp, remoteFp: o.remote_fp, name: o.name, roster: o.roster,
    now: o.clock,
  }), V.hellos.owner.signed);
  const atOwner = await verifyHello(V.hellos.phone.signed, {
    roster: full, expectDid: phone.did, myLocalFp: o.local_fp, myRemoteFp: o.remote_fp, now: V.now,
  });
  const atPhoneSide = await verifyHello(V.hellos.owner.signed, {
    roster: atPhone, expectDid: owner.did, myLocalFp: p.local_fp, myRemoteFp: p.remote_fp, now: V.now,
  });
  assert.ok(atOwner.ok, atOwner.reason);
  assert.ok(atPhoneSide.ok, atPhoneSide.reason);
});

test('a hello that does not hold fails for the same reason as in Python', async () => {
  const atPhone = await roster(V.admissions.phone);
  const o = V.hellos.owner.args;
  const p = V.hellos.phone.args;
  const see = { roster: atPhone, myLocalFp: p.local_fp, myRemoteFp: p.remote_fp, now: V.now };
  const check = async (hello, extra) => (await verifyHello(hello, { ...see, ...extra })).reason;
  assert.equal(await check(V.hellos.owner.signed, { expectDid: tablet.did }), 'this is not the device this link is for');
  assert.equal(await check(V.hellos.owner.signed, { expectDid: owner.did, myRemoteFp: 'ee'.repeat(32) }),
    'the fingerprints do not match: someone is between the two devices');
  assert.equal(await check(V.hellos.owner.signed, { expectDid: owner.did, now: V.now + 500 }),
    'the hello is too old or from the future');
  assert.equal(await check({ ...V.hellos.owner.signed, name: 'x' }, { expectDid: owner.did }),
    "the signature is not the sender's");
  // the tablet, unknown to this phone's roster and without its admission, is no member
  const bare = await createHello(tablet, {
    mesh: V.mesh, nonce: o.nonce, localFp: o.local_fp, remoteFp: o.remote_fp, now: V.now,
  });
  assert.equal(await check(bare, { expectDid: tablet.did }), 'not a device of this mesh');
  // ...and with it, it is
  const withAdmission = await createHello(tablet, {
    mesh: V.mesh, nonce: o.nonce, localFp: o.local_fp, remoteFp: o.remote_fp, admission: V.admissions.tablet, now: V.now,
  });
  assert.equal(await check(withAdmission, { expectDid: tablet.did }), null);
  assert.equal(await check('junk', { expectDid: owner.did }), 'not a hello');
});

test('a frame Python signed, with a time_ns() counter, decodes here exactly', async () => {
  const f = await decodeFrame(V.frame.text, { allow: [owner.did], selfDid: phone.did });
  assert.ok(f);
  assert.equal(f.from, owner.did);
  assert.equal(f.ctr, BigInt(V.frame.ctr)); // past 2^53, every digit kept
  assert.deepEqual(f.message, base64ToBytes(V.frame.message_b64));
  const inner = muxDecode(f.message);
  assert.equal(inner.channel, V.frame.channel);
  assert.equal(new TextDecoder().decode(inner.payload), V.frame.payload);
  // not for us, or from someone we do not allow: nothing
  assert.equal(await decodeFrame(V.frame.text, { allow: [owner.did], selfDid: tablet.did }), null);
  assert.equal(await decodeFrame(V.frame.text, { allow: [tablet.did], selfDid: phone.did }), null);
  assert.equal(await decodeFrame(V.frame.text.replace('"to"', '"to_"'), { allow: [owner.did], selfDid: phone.did }), null);
});

test('the browser signs the very frame Python signs', async () => {
  const message = muxEncode(V.frame.channel, new TextEncoder().encode(V.frame.payload));
  assert.deepEqual(message, base64ToBytes(V.frame.message_b64));
  const text = await encodeFrame(owner, phone.did, message, BigInt(V.frame.ctr));
  assert.deepEqual(parseJson(text), parseJson(V.frame.text));
  const back = await decodeFrame(text, { allow: [owner.did], selfDid: phone.did });
  assert.equal(back.ctr, BigInt(V.frame.ctr));
});

test('counters rise across reloads, and the replay window takes each once', () => {
  const next = newCounter();
  const a = next();
  assert.ok(next() > a && a > BigInt(Date.now() - 1000) * 1_000_000n);
  const w = new ReplayWindow(4);
  assert.equal(w.accept(10n), true);
  assert.equal(w.accept(10n), false); // a replay
  assert.equal(w.accept(8n), true); // late but inside the window
  assert.equal(w.accept(8n), false);
  assert.equal(w.accept(20n), true);
  assert.equal(w.accept(16n), false); // too far behind now
  assert.equal(w.accept(17n), true);
});
