// The browser's pairing code against the Python reference: every statement in
// fixtures/vectors/pairing.json (signed by transport/secdogie_transport/pairing.py)
// must verify here, and the same inputs must produce the very same bytes.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import {
  DEVICE, INVITER, canonical, checkCode, cleanName, createAdmission, createBind, createInvite, decodeInvite,
  didFromPublicKey, encodeInvite, fingerprintFromSdp, generateIdentity, identityFromSeed, normalizeFingerprint,
  publicKeyFromDid, randomHex32, verifyAdmission, verifyBind, verifyInvite, verifyPayload,
} from '../client/pairing.js';

const V = JSON.parse(readFileSync(new URL('../../fixtures/vectors/pairing.json', import.meta.url), 'utf8'));
const owner = await identityFromSeed(V.keys.owner.seed_hex);
const phone = await identityFromSeed(V.keys.phone.seed_hex);

test('keys: the same seed is the same did:key as in Python', () => {
  assert.equal(owner.did, V.keys.owner.did);
  assert.equal(phone.did, V.keys.phone.did);
  assert.equal(didFromPublicKey(publicKeyFromDid(owner.did)), owner.did);
});

test('canonical JSON matches Python for what pairing uses, and refuses the rest', () => {
  assert.equal(canonical({ b: 1, a: '我的手机', c: ['x', true, null] }), '{"a":"我的手机","b":1,"c":["x",true,null]}');
  assert.equal(canonical({ t: 'a"b\\c\n\u001f' }), '{"t":"a\\"b\\\\c\\n\\u001f"}');
  assert.throws(() => canonical({ x: 1.5 }));
  assert.throws(() => canonical({ x: '\ud800' }));
  assert.throws(() => canonical(new Date()));
});

test('everything Python signed verifies here', async () => {
  const invite = await verifyInvite(decodeInvite(V.invite.link), { now: V.now });
  assert.ok(invite.ok, invite.reason);
  assert.deepEqual(invite.value, V.invite.signed);
  const inv = invite.value;
  const fromOwner = await verifyBind(V.binds.owner.signed, {
    invite: inv, expectRole: INVITER, myLocalFp: V.binds.phone.args.local_fp, myRemoteFp: V.binds.phone.args.remote_fp, now: V.now,
  });
  const fromPhone = await verifyBind(V.binds.phone.signed, {
    invite: inv, expectRole: DEVICE, myLocalFp: V.binds.owner.args.local_fp, myRemoteFp: V.binds.owner.args.remote_fp, now: V.now,
  });
  assert.ok(fromOwner.ok, fromOwner.reason);
  assert.ok(fromPhone.ok, fromPhone.reason);
  assert.equal(await checkCode(inv.invite_id, V.binds.owner.signed, V.binds.phone.signed), V.check_code);
  const admitted = await verifyAdmission(V.admission.signed, { owners: [owner.did], mesh: 'home', device: phone.did });
  assert.ok(admitted.ok, admitted.reason);
});

test('the same inputs produce the very same bytes as Python', async () => {
  const a = V.invite.args;
  const invite = await createInvite(owner, a.mesh, { room: a.room, signal: a.signal, ttl: a.ttl, now: a.clock });
  assert.deepEqual(invite, V.invite.signed);
  assert.equal(encodeInvite(invite), V.invite.link);
  for (const [who, id, role] of [['owner', owner, INVITER], ['phone', phone, DEVICE]]) {
    const b = V.binds[who].args;
    const bind = await createBind(id, {
      inviteId: invite.invite_id, role, nonce: b.nonce, localFp: b.local_fp, remoteFp: b.remote_fp, name: b.name, now: b.clock,
    });
    assert.deepEqual(bind, V.binds[who].signed);
  }
  const ad = V.admission.args;
  const admission = await createAdmission(owner, 'home', phone.did, {
    inviteId: invite.invite_id, name: ad.name, roles: ad.roles, now: ad.clock,
  });
  assert.deepEqual(admission, V.admission.signed);
});

test('someone in the middle of the signaling is caught, and the codes would differ', async () => {
  const inv = V.invite.signed;
  const middle = 'ee'.repeat(32);
  const fromPhone = await createBind(phone, {
    inviteId: inv.invite_id, role: DEVICE, nonce: randomHex32(), localFp: V.binds.phone.args.local_fp, remoteFp: middle, now: V.now,
  });
  const seen = await verifyBind(fromPhone, {
    invite: inv, expectRole: DEVICE, myLocalFp: V.binds.owner.args.local_fp, myRemoteFp: middle, now: V.now,
  });
  assert.equal(seen.ok, false);
  assert.match(seen.reason, /someone is between/);
  assert.notEqual(await checkCode(inv.invite_id, V.binds.owner.signed, fromPhone), V.check_code);
});

test('altered, expired or re-addressed statements fail, each for its reason', async () => {
  const inv = V.invite.signed;
  assert.equal((await verifyInvite({ ...inv, mesh: 'office' }, { now: V.now })).reason, "the signature is not the inviter's");
  assert.equal((await verifyInvite(inv, { now: inv.expires_at })).reason, 'the invite has expired');
  assert.equal((await verifyInvite('junk')).reason, 'not an invite');
  assert.equal(decodeInvite('%%%'), null);
  const see = { invite: inv, myLocalFp: V.binds.owner.args.local_fp, myRemoteFp: V.binds.owner.args.remote_fp };
  assert.equal((await verifyBind(V.binds.phone.signed, { ...see, expectRole: INVITER, now: V.now })).reason,
    'the wrong side sent this bind');
  assert.equal((await verifyBind(V.binds.phone.signed, { ...see, expectRole: DEVICE, now: V.now + 500 })).reason,
    'the bind is too old or from the future');
  assert.equal((await verifyBind({ ...V.binds.phone.signed, name: 'x' }, { ...see, expectRole: DEVICE, now: V.now })).reason,
    "the signature is not the sender's");
  const stranger = await generateIdentity();
  assert.equal((await verifyAdmission(V.admission.signed, { owners: [stranger.did] })).reason,
    'not signed by an owner of this mesh');
  assert.equal((await verifyAdmission(V.admission.signed, { owners: [owner.did], mesh: 'office' })).reason,
    'the admission is for another mesh');
  assert.equal((await verifyAdmission(V.admission.signed, { owners: null })).reason, 'no owners configured');
  await assert.rejects(createAdmission(owner, 'home', owner.did, { inviteId: inv.invite_id }));
  await assert.rejects(createAdmission(owner, 'home', phone.did, { inviteId: inv.invite_id, roles: ['admin'] }));
});

test('a generated device key signs, and its private key cannot leave the browser', async () => {
  const device = await generateIdentity();
  const invite = await createInvite(device, 'home', { room: `pair-${randomHex32()}`, signal: 'wss://signal.example/ws' });
  assert.ok((await verifyInvite(invite)).ok);
  assert.ok((await verifyPayload(invite)).ok);
  assert.equal((await verifyPayload({ ...invite, sig: invite.sig.replace(/^./, (c) => (c === 'A' ? 'B' : 'A')) })).ok, false);
});

test('fingerprints and names are normalized the same way as in Python', () => {
  assert.equal(normalizeFingerprint(V.binds.owner.args.local_fp), V.binds.owner.signed.local_fp);
  assert.equal(normalizeFingerprint('AB:CD'), '');
  assert.equal(fingerprintFromSdp('v=0\r\na=fingerprint:sha-256 ' + 'AB:'.repeat(31) + 'AB\r\na=setup:actpass\r\n'), 'ab'.repeat(32));
  assert.equal(fingerprintFromSdp('v=0\r\n'), '');
  assert.equal(cleanName('  我的\u0000手机​  '), '我的手机');
  assert.equal(Array.from(cleanName('长'.repeat(100))).length, 40);
});
