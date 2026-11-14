// Pairing one of your own devices into your mesh: the browser's side.
//
// This mirrors transport/secdogie_transport/pairing.py statement for
// statement, and fixtures/vectors/pairing.json locks the two together byte
// for byte:
//
//   invite     a short-lived signed link from a device already in the mesh
//   bind       each side signs the DTLS fingerprints it sees, so someone in
//              the middle of the signaling is caught
//   check code six digits both screens show; the person compares them
//   admission  the inviter signs "this DID is a device of this mesh"
//
// Pure functions over WebCrypto (Ed25519, SHA-256): no DOM, no network, no
// storage. Every field is a string or an integer, never a float, so the
// canonical JSON is the same bytes as Python's.

export const INVITE_TYPE = 'secdogie/device-invite/v1';
export const BIND_TYPE = 'secdogie/carrier-bind/v1';
export const SAS_TYPE = 'secdogie/pair-sas/v1';
export const ADMISSION_TYPE = 'secdogie/device-admission/v1';
export const INVITER = 'inviter';
export const DEVICE = 'device';
export const DEFAULT_TTL = 600;
export const MAX_TTL = 3600;
export const MAX_SKEW = 120;
export const MAX_NAME = 40;
export const ROLES = Object.freeze(['forward', 'serve', 'model', 'relay', 'ingest']);

const ROOM = /^[A-Za-z0-9_-]{1,64}$/;
const HEX32 = /^[0-9a-f]{32}$/;
const FP = /^[0-9a-f]{64}$/;
const SIGNAL = /^wss?:\/\/[A-Za-z0-9.\-[\]:]+(\/[A-Za-z0-9._~/-]*)?$/;
const subtle = globalThis.crypto.subtle;
const utf8 = new TextEncoder();

// ---- canonical JSON: json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) ----

function byCodePoint(a, b) {
  const x = Array.from(a);
  const y = Array.from(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0) - y[i].codePointAt(0);
    if (d !== 0) return d;
  }
  return x.length - y.length;
}

/** The canonical JSON text of `value`. Refuses what Python would print differently. */
export function canonical(value) {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'string') {
    if (!value.isWellFormed()) throw new TypeError('a string with a lone surrogate has no canonical form');
    return JSON.stringify(value);
  }
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) throw new TypeError('only safe integers have a canonical form here');
    return String(value);
  }
  if (typeof value === 'bigint') return value.toString(); // a frame counter is a time_ns() value
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
    const keys = Object.keys(value).sort(byCodePoint);
    return `{${keys.map((k) => `${canonical(k)}:${canonical(value[k])}`).join(',')}}`;
  }
  throw new TypeError('not a JSON value');
}

// ---- bytes ----

const hex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
const unhex = (text) => Uint8Array.from(text.match(/../g) ?? [], (h) => parseInt(h, 16));
const b64 = (bytes) => {
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
};
const unb64 = (text) => Uint8Array.from(atob(text), (c) => c.charCodeAt(0));

async function sha256(bytes) {
  return new Uint8Array(await subtle.digest('SHA-256', bytes));
}

/** Lower-case hex SHA-256 of `bytes` (or of a string's UTF-8). */
export async function sha256Hex(data) {
  return hex(await sha256(typeof data === 'string' ? utf8.encode(data) : data));
}

export const bytesToBase64 = (bytes) => b64(bytes);
export const base64ToBytes = (text) => unb64(text);

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

function base58(bytes) {
  let n = 0n;
  for (const b of bytes) n = (n << 8n) | BigInt(b);
  let out = '';
  while (n > 0n) {
    out = B58[Number(n % 58n)] + out;
    n /= 58n;
  }
  for (const b of bytes) {
    if (b !== 0) break;
    out = '1' + out;
  }
  return out;
}

function unbase58(text) {
  let n = 0n;
  for (const c of text) {
    const i = B58.indexOf(c);
    if (i < 0) throw new TypeError('not base58');
    n = n * 58n + BigInt(i);
  }
  const out = [];
  while (n > 0n) {
    out.unshift(Number(n & 0xffn));
    n >>= 8n;
  }
  for (const c of text) {
    if (c !== '1') break;
    out.unshift(0);
  }
  return Uint8Array.from(out);
}

// ---- identities: did:key over Ed25519 ----

/** did:key for a raw 32-byte Ed25519 public key (multicodec 0xed01, base58btc). */
export function didFromPublicKey(raw) {
  return `did:key:z${base58(Uint8Array.from([0xed, 0x01, ...raw]))}`;
}

export function publicKeyFromDid(did) {
  if (typeof did !== 'string' || !did.startsWith('did:key:z')) throw new TypeError('not a did:key');
  const bytes = unbase58(did.slice('did:key:z'.length));
  if (bytes.length !== 34 || bytes[0] !== 0xed || bytes[1] !== 0x01) throw new TypeError('not an Ed25519 did:key');
  return bytes.slice(2);
}

/** An identity from a WebCrypto key pair: `{did, sign(bytes)}`. The private key can be non-extractable. */
export async function identityFromKeyPair(pair) {
  const raw = new Uint8Array(await subtle.exportKey('raw', pair.publicKey));
  return {
    did: didFromPublicKey(raw),
    keyPair: pair, // kept so a page can store it (IndexedDB clones CryptoKeys without exporting them)
    sign: async (bytes) => new Uint8Array(await subtle.sign('Ed25519', pair.privateKey, bytes)),
  };
}

/** A fresh device identity whose private key cannot be exported from this browser. */
export async function generateIdentity() {
  return identityFromKeyPair(await subtle.generateKey('Ed25519', false, ['sign', 'verify']));
}

const PKCS8_ED25519 = unhex('302e020100300506032b657004220420');

/** An identity from a 32-byte seed. For tests and the golden vectors only. */
export async function identityFromSeed(seedHex) {
  const pkcs8 = Uint8Array.from([...PKCS8_ED25519, ...unhex(seedHex)]);
  const priv = await subtle.importKey('pkcs8', pkcs8, 'Ed25519', true, ['sign']);
  const jwk = await subtle.exportKey('jwk', priv);
  const raw = unb64(jwk.x.replace(/-/g, '+').replace(/_/g, '/'));
  return {
    did: didFromPublicKey(raw),
    sign: async (bytes) => new Uint8Array(await subtle.sign('Ed25519', priv, bytes)),
  };
}

/** `payload` plus `signer` and `sig`: a detached Ed25519 signature over its canonical JSON. */
export async function signPayload(identity, payload) {
  if ('signer' in payload || 'sig' in payload) throw new TypeError('the payload already carries a signature');
  const sig = await identity.sign(utf8.encode(canonical(payload)));
  return { ...payload, signer: identity.did, sig: b64(sig) };
}

/** `{ok, signer}`: whether `obj` carries a valid signature by the key its `signer` names. */
export async function verifyPayload(obj) {
  try {
    const { signer, sig, ...payload } = obj;
    if (typeof signer !== 'string' || typeof sig !== 'string') return { ok: false, signer: null };
    const key = await subtle.importKey('raw', publicKeyFromDid(signer), 'Ed25519', true, ['verify']);
    const ok = await subtle.verify('Ed25519', key, unb64(sig), utf8.encode(canonical(payload)));
    return { ok, signer: ok ? signer : null };
  } catch {
    return { ok: false, signer: null };
  }
}

// ---- small checks shared by the statements ----

const isInt = (v) => Number.isSafeInteger(v);
const fail = (reason) => ({ ok: false, reason, value: null });
const pass = (value) => ({ ok: true, reason: null, value });
const nowSeconds = () => Math.floor(Date.now() / 1000);

/** A device name: printable characters only, trimmed, at most MAX_NAME of them. */
export function cleanName(name) {
  const text = String(name ?? '').replace(/[\p{C}\p{Zl}\p{Zp}]|(?! )\p{Zs}/gu, '').replace(/^ +| +$/g, '');
  return Array.from(text).slice(0, MAX_NAME).join('');
}

/** A DTLS fingerprint as 64 lower-case hex digits; '' unless it is a SHA-256 one. */
export function normalizeFingerprint(text) {
  if (typeof text !== 'string') return '';
  let t = text.trim().toLowerCase();
  if (t.startsWith('sha-256')) t = t.slice('sha-256'.length).trim();
  t = t.replaceAll(':', '');
  return FP.test(t) ? t : '';
}

/** The SHA-256 DTLS fingerprint an SDP carries (`a=fingerprint:sha-256 AB:CD:...`), normalized. */
export function fingerprintFromSdp(sdp) {
  const m = /^a=fingerprint:(sha-256 [0-9A-Fa-f:]+)\s*$/m.exec(sdp ?? '');
  return m ? normalizeFingerprint(m[1]) : '';
}

// ---- the invite ----

export async function createInvite(inviter, mesh, { room, signal, ttl = DEFAULT_TTL, now = nowSeconds() }) {
  if (typeof mesh !== 'string' || !mesh || Array.from(mesh).length > 64) throw new TypeError('an invite names its mesh');
  if (typeof room !== 'string' || !ROOM.test(room)) throw new TypeError('the room is 1-64 letters, digits, - or _');
  if (typeof signal !== 'string' || !SIGNAL.test(signal)) throw new TypeError('the signaling address is a ws:// or wss:// URL');
  if (!isInt(ttl) || ttl <= 0 || ttl > MAX_TTL) throw new TypeError(`an invite lives between 1 and ${MAX_TTL} seconds`);
  const body = { type: INVITE_TYPE, mesh, inviter: inviter.did, room, signal, expires_at: now + ttl };
  body.invite_id = hex(await sha256(utf8.encode(canonical(body)))).slice(0, 32);
  return signPayload(inviter, body);
}

/** The invite as it travels in a link: base64url of its canonical JSON. */
export function encodeInvite(signed) {
  return b64(utf8.encode(canonical(signed))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function decodeInvite(text) {
  try {
    const raw = unb64(String(text).replace(/-/g, '+').replace(/_/g, '/'));
    const obj = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
    return obj && typeof obj === 'object' && !Array.isArray(obj) ? obj : null;
  } catch {
    return null;
  }
}

export async function verifyInvite(obj, { now = nowSeconds() } = {}) {
  if (!obj || typeof obj !== 'object' || obj.type !== INVITE_TYPE) return fail('not an invite');
  const { ok, signer } = await verifyPayload(obj);
  if (!ok || obj.inviter !== signer) return fail("the signature is not the inviter's");
  const { mesh, room, signal, expires_at: exp } = obj;
  if (typeof mesh !== 'string' || !mesh || Array.from(mesh).length > 64) return fail('no mesh name');
  if (typeof room !== 'string' || !ROOM.test(room) || typeof signal !== 'string' || !SIGNAL.test(signal)) {
    return fail('bad room or signaling address');
  }
  if (!isInt(exp)) return fail('bad expiry');
  const body = { type: obj.type, mesh, inviter: obj.inviter, room, signal, expires_at: exp };
  if (obj.invite_id !== hex(await sha256(utf8.encode(canonical(body)))).slice(0, 32)) {
    return fail('the invite id does not match its content');
  }
  if (now >= exp) return fail('the invite has expired');
  return pass(obj);
}

// ---- the bind: this channel belongs to these two keys ----

export async function createBind(identity, { inviteId, role, nonce, localFp, remoteFp, name = '', now = nowSeconds() }) {
  const local = normalizeFingerprint(localFp);
  const remote = normalizeFingerprint(remoteFp);
  if (role !== INVITER && role !== DEVICE) throw new TypeError('role is inviter or device');
  if (!HEX32.test(inviteId ?? '') || !HEX32.test(nonce ?? '')) throw new TypeError('invite_id and nonce are 32 hex digits');
  if (!local || !remote) throw new TypeError('both fingerprints are SHA-256 fingerprints');
  return signPayload(identity, {
    type: BIND_TYPE, invite_id: inviteId, did: identity.did, role, nonce, local_fp: local, remote_fp: remote,
    name: cleanName(name), ts: now,
  });
}

export async function verifyBind(obj, { invite, expectRole, myLocalFp, myRemoteFp, now = nowSeconds() }) {
  if (!obj || typeof obj !== 'object' || obj.type !== BIND_TYPE) return fail('not a bind');
  const { ok, signer } = await verifyPayload(obj);
  if (!ok || obj.did !== signer) return fail("the signature is not the sender's");
  if (obj.invite_id !== invite.invite_id) return fail('the bind is for another invite');
  if (obj.role !== expectRole) return fail('the wrong side sent this bind');
  if (expectRole === INVITER && signer !== invite.inviter) return fail('the other side is not the inviter');
  if (expectRole === DEVICE && signer === invite.inviter) return fail('a device cannot pair with itself');
  if (typeof obj.nonce !== 'string' || !HEX32.test(obj.nonce) || !isInt(obj.ts)) return fail('bad nonce or time');
  if (obj.name !== cleanName(obj.name)) return fail('bad device name');
  const mine = normalizeFingerprint(myLocalFp);
  const theirs = normalizeFingerprint(myRemoteFp);
  if (!mine || !theirs) return fail('this side has no fingerprints yet');
  if (obj.local_fp !== theirs || obj.remote_fp !== mine) {
    return fail('the fingerprints do not match: someone is between the two devices');
  }
  if (Math.abs(now - obj.ts) > MAX_SKEW) return fail('the bind is too old or from the future');
  return pass(obj);
}

/** The six digits both screens show. */
export async function checkCode(inviteId, inviterBind, deviceBind) {
  const part = (b) => ({ did: b.did, nonce: b.nonce, local_fp: b.local_fp });
  const digest = await sha256(utf8.encode(canonical({
    type: SAS_TYPE, invite_id: inviteId, inviter: part(inviterBind), device: part(deviceBind),
  })));
  const n = ((digest[0] << 24) | (digest[1] << 16) | (digest[2] << 8) | digest[3]) >>> 0;
  return String(n % 1_000_000).padStart(6, '0');
}

// ---- the admission: this DID is a device of this mesh ----

export async function createAdmission(owner, mesh, deviceDid, { inviteId, name = '', roles = ['forward'], now = nowSeconds() }) {
  publicKeyFromDid(deviceDid);
  if (deviceDid === owner.did) throw new TypeError('a device does not admit itself');
  const sorted = [...new Set(roles)].sort();
  if (!sorted.length || sorted.some((r) => !ROLES.includes(r))) throw new TypeError(`roles are among ${ROLES.join(', ')}`);
  if (!HEX32.test(inviteId ?? '')) throw new TypeError('invite_id is 32 hex digits');
  return signPayload(owner, {
    type: ADMISSION_TYPE, mesh, device: deviceDid, name: cleanName(name), roles: sorted, invite_id: inviteId,
    admitted_at: now,
  });
}

/** `owners`: the DIDs that may admit devices to this mesh (an array or a Set). */
export async function verifyAdmission(obj, { owners, mesh = null, device = null }) {
  if (!obj || typeof obj !== 'object' || obj.type !== ADMISSION_TYPE) return fail('not an admission');
  if (owners == null) return fail('no owners configured');
  const { ok, signer } = await verifyPayload(obj);
  if (!ok) return fail('invalid or missing signature');
  if (!new Set(owners).has(signer)) return fail('not signed by an owner of this mesh');
  if (mesh !== null && obj.mesh !== mesh) return fail('the admission is for another mesh');
  if (typeof obj.device !== 'string' || obj.device === signer || (device !== null && obj.device !== device)) {
    return fail('the admission is for another device');
  }
  const roles = obj.roles;
  const sorted = Array.isArray(roles) ? [...new Set(roles)].sort() : [];
  if (!sorted.length || sorted.some((r) => !ROLES.includes(r)) || canonical(roles) !== canonical(sorted)) {
    return fail('bad roles');
  }
  if (!isInt(obj.admitted_at) || obj.name !== cleanName(obj.name)) return fail('bad admission');
  return pass(obj);
}

/** 32 random hex digits: a nonce, or the tail of a one-time room name. */
export function randomHex32() {
  return hex(globalThis.crypto.getRandomValues(new Uint8Array(16)));
}
