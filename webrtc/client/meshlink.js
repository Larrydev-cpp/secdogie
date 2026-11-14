// Your own devices as one mesh: the browser's side of
// transport/secdogie_transport/meshlink.py, held to it byte for byte by
// fixtures/vectors/meshlink.json.
//
//   linkRoom   the signaling room for one pair of devices; both ends work it
//              out, and the gateway's rooms of two fit exactly
//   Roster     the owners (local trust) and the admissions they signed
//   hello      what each end of a link signs: who it is, its admission, and
//              the DTLS fingerprints it sees of this very connection
//
// Pure functions and a small class over WebCrypto: no DOM, no network.

import {
  ADMISSION_TYPE, canonical, cleanName, normalizeFingerprint, sha256Hex, signPayload, verifyAdmission, verifyPayload,
} from './pairing.js';

export const ROOM_DOMAIN = 'secdogie/link-room/v1';
export const HELLO_TYPE = 'secdogie/link-hello/v1';
export const MESH_CHANNEL = 'mesh/v1';
export const MAX_SKEW = 120;

const HEX32 = /^[0-9a-f]{32}$/;
const DIGEST = /^([0-9a-f]{16})?$/;
const fail = (reason) => ({ ok: false, reason, value: null });
const pass = (value) => ({ ok: true, reason: null, value });
const nowSeconds = () => Math.floor(Date.now() / 1000);
const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0); // DIDs and base64 are ASCII: same order as Python

/** The signaling room for the link between devices `a` and `b` of `mesh`. */
export async function linkRoom(mesh, a, b) {
  if (typeof mesh !== 'string' || !mesh || typeof a !== 'string' || typeof b !== 'string' || !a || !b) {
    throw new TypeError('a link room needs a mesh and two DIDs');
  }
  if (a === b) throw new TypeError('a device has no link to itself');
  const [lo, hi] = [a, b].sort(byText);
  return `l-${(await sha256Hex(`${ROOM_DOMAIN}|${mesh}|${lo}|${hi}`)).slice(0, 40)}`;
}

/** The owners of a mesh and the admissions they signed. */
export class Roster {
  #admissions = new Map();

  constructor(mesh, owners) {
    if (typeof mesh !== 'string' || !mesh) throw new TypeError('a roster names its mesh');
    this.mesh = mesh;
    this.owners = new Set(owners);
    if (!this.owners.size) throw new TypeError('a roster needs at least one owner');
  }

  /** Keep an admission an owner signed for this mesh; true when it added or refreshed a device. */
  async admit(admission) {
    const checked = await verifyAdmission(admission, { owners: [...this.owners], mesh: this.mesh });
    if (!checked.ok) return false;
    const held = this.#admissions.get(checked.value.device);
    if (held && held.admitted_at >= checked.value.admitted_at) return false;
    this.#admissions.set(checked.value.device, checked.value);
    return true;
  }

  isMember(did) {
    return this.owners.has(did) || this.#admissions.has(did);
  }

  devices() {
    return [...new Set([...this.owners, ...this.#admissions.keys()])].sort(byText);
  }

  admission(did) {
    return this.#admissions.get(did) ?? null;
  }

  name(did) {
    return this.#admissions.get(did)?.name ?? '';
  }

  /** What a device hands its neighbours: the admissions. Owners never travel. */
  wire() {
    return { mesh: this.mesh, admissions: [...this.#admissions.keys()].sort(byText).map((d) => this.#admissions.get(d)) };
  }

  /** Take a neighbour's roster; resolves how many devices it added or refreshed. */
  async merge(wire) {
    if (!wire || wire.mesh !== this.mesh || !Array.isArray(wire.admissions)) return 0;
    let n = 0;
    for (const a of wire.admissions.slice(0, 256)) {
      if (a && typeof a === 'object' && (await this.admit(a))) n += 1;
    }
    return n;
  }

  /** 16 hex digits naming what this roster holds. */
  async digest() {
    const body = {
      mesh: this.mesh,
      owners: [...this.owners].sort(byText),
      admissions: [...this.#admissions.values()].map((a) => a.sig).sort(byText),
    };
    return (await sha256Hex(canonical(body))).slice(0, 16);
  }
}

/** Sign who this device is and what it sees of this connection. */
export async function createHello(identity, {
  mesh, nonce, localFp, remoteFp, admission = null, name = '', roster = '', now = nowSeconds(),
}) {
  const local = normalizeFingerprint(localFp);
  const remote = normalizeFingerprint(remoteFp);
  if (!HEX32.test(nonce ?? '')) throw new TypeError('the nonce is 32 hex digits');
  if (!local || !remote) throw new TypeError('both fingerprints are SHA-256 fingerprints');
  if (admission !== null && admission?.type !== ADMISSION_TYPE) {
    throw new TypeError('admission is a signed device admission, or null for an owner');
  }
  if (typeof roster !== 'string' || !DIGEST.test(roster)) throw new TypeError('roster is a roster digest, or empty');
  return signPayload(identity, {
    type: HELLO_TYPE, mesh, did: identity.did, nonce, local_fp: local, remote_fp: remote, name: cleanName(name),
    roster, admission, ts: now,
  });
}

/**
 * Check the other end of a link. `expectDid` is the device this link is for.
 * The fingerprints must cross-match: a mismatch means someone is between the
 * two devices.
 */
export async function verifyHello(obj, { roster, expectDid, myLocalFp, myRemoteFp, now = nowSeconds() }) {
  if (!obj || typeof obj !== 'object' || obj.type !== HELLO_TYPE) return fail('not a hello');
  const { ok, signer } = await verifyPayload(obj);
  if (!ok || obj.did !== signer) return fail("the signature is not the sender's");
  if (signer !== expectDid) return fail('this is not the device this link is for');
  if (obj.mesh !== roster.mesh) return fail('the hello is for another mesh');
  let member = roster.isMember(signer);
  if (!member && obj.admission && typeof obj.admission === 'object') {
    member = (await verifyAdmission(obj.admission, { owners: [...roster.owners], mesh: roster.mesh, device: signer })).ok;
  }
  if (!member) return fail('not a device of this mesh');
  if (typeof obj.nonce !== 'string' || !HEX32.test(obj.nonce) || !Number.isSafeInteger(obj.ts)
      || obj.name !== cleanName(obj.name) || typeof obj.roster !== 'string' || !DIGEST.test(obj.roster)) {
    return fail('bad hello');
  }
  const mine = normalizeFingerprint(myLocalFp);
  const theirs = normalizeFingerprint(myRemoteFp);
  if (!mine || !theirs) return fail('this side has no fingerprints yet');
  if (obj.local_fp !== theirs || obj.remote_fp !== mine) {
    return fail('the fingerprints do not match: someone is between the two devices');
  }
  if (Math.abs(now - obj.ts) > MAX_SKEW) return fail('the hello is too old or from the future');
  return pass(obj);
}
