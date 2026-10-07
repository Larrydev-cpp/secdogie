/**
 * The page's side of the browser link's statements -- the twin of
 * `identity/secdogie_identity/linkauth.py`, byte for byte (fixtures/vectors/link.json).
 *
 * W1: each end signs, with its DID, the DTLS fingerprints of its own SDP and of
 * the peer's SDP as it received it. The page checks the node's statement first
 * and reveals nothing (not even its DID) until it verifies:
 *
 *   what I see as yours  must be among what you say is yours, and
 *   what you saw as mine must be among what I actually have,
 *
 * every set non-empty with at least one sha-256 / sha-384 / sha-512 entry. A
 * subset, not equality: Chromium keeps one of aiortc's three fingerprints.
 *
 * Pairing: a one-time link (node DID + 32-byte secret; no address -- the page's
 * own build names the gateway), a signed and HMAC'd hello, a 12-digit check
 * code both ends derive from the same signed bytes, a tap that sends the
 * confirmation (with the operator key's proof when one is offered), and the
 * node's signed receipt or refusal.
 */

import { type CanonObject, PyFloat, canonicalBytes, isCanonObject, pyFloat, toHex } from '../core/canon.ts';
import { isDidKey } from '../core/did.ts';
import type { Signer } from '../core/ed25519.ts';
import { TrustSet, signPayload, verifyPayload } from '../core/envelope.ts';

export const LINK_BINDING_TYPE = 'secdogie/webrtc-dtls-binding/v1';
export const PAIR_HELLO_TYPE = 'secdogie/webrtc-pair-hello/v1';
export const PAIR_CONFIRM_TYPE = 'secdogie/webrtc-pair-confirm/v1';
export const PAIR_OPERATOR_TYPE = 'secdogie/webrtc-pair-operator/v1';
export const PAIRED_TYPE = 'secdogie/webrtc-paired/v1';
export const REFUSED_TYPE = 'secdogie/webrtc-refused/v1';

export const MAX_SKEW = 120;
export const SECRET_BYTES = 32;
export const ROOM_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
export const REFUSAL_REASONS = ['busy', 'not-enrolled', 'pairing-rejected', 'pairing-unavailable'] as const;
export type RefusalReason = (typeof REFUSAL_REASONS)[number];

const STRONG = new Set(['sha-256', 'sha-384', 'sha-512']);
const FP_LINE = /^a=fingerprint:(\S+)[ \t]+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2})+)[ \t]*\r?$/gm;
const M_LINE = /^m=(\S+)[ \t]+\S+[ \t]+(\S+)/gm;
const FP_NORMAL = /^[0-9A-F]{2}(?::[0-9A-F]{2})+$/;

// ---- bytes ------------------------------------------------------------------------------

export function b64url(bytes: Uint8Array): string {
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function b64urlDecode(text: string): Uint8Array<ArrayBuffer> {
  if (!/^[A-Za-z0-9_-]*$/.test(text) || text.length % 4 === 1) throw new Error('not unpadded base64url');
  const bin = atob(text.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(text.length / 4) * 4, '='));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function sha256(...parts: Uint8Array[]): Promise<Uint8Array<ArrayBuffer>> {
  const len = parts.reduce((n, p) => n + p.length, 0);
  const buf = new Uint8Array(len);
  let off = 0;
  for (const p of parts) {
    buf.set(p, off);
    off += p.length;
  }
  return new Uint8Array(await crypto.subtle.digest('SHA-256', buf));
}

const utf8 = (s: string) => new TextEncoder().encode(s);

function checkSecret(secret: Uint8Array): void {
  if (secret.length !== SECRET_BYTES) throw new Error(`a pairing secret is ${SECRET_BYTES} bytes`);
}

// ---- SDP --------------------------------------------------------------------------------

/** Every a=fingerprint line, normalized (`alg UPPERHEX`), de-duplicated and sorted. */
export function fingerprintsFromSdp(sdp: string): string[] {
  if (typeof sdp !== 'string') throw new Error('an SDP is text');
  const found = new Set<string>();
  for (const m of sdp.matchAll(FP_LINE)) found.add(`${m[1]!.toLowerCase()} ${m[2]!.toUpperCase()}`);
  if (found.size === 0) throw new Error('the SDP carries no DTLS fingerprint');
  if (![...found].some((fp) => STRONG.has(fp.split(' ')[0]!))) {
    throw new Error('the SDP carries no sha-256 (or stronger) fingerprint');
  }
  return sortCodePoints([...found]);
}

/** Every media section is an SCTP data channel, and there is one. */
export function sdpIsDataOnly(sdp: string): boolean {
  if (typeof sdp !== 'string') return false;
  const lines = [...sdp.matchAll(M_LINE)];
  return lines.length > 0 && lines.every((m) => m[1] === 'application' && m[2]!.toUpperCase().includes('DTLS/SCTP'));
}

export function fingerprintsAgree(o: {
  declaredLocal: readonly string[];
  declaredRemote: readonly string[];
  observedLocal: readonly string[];
  observedRemote: readonly string[];
}): string | null {
  const sets: Record<string, Set<string>> = {};
  for (const [name, v] of [
    ['declared local', o.declaredLocal],
    ['declared remote', o.declaredRemote],
    ['observed local', o.observedLocal],
    ['observed remote', o.observedRemote],
  ] as const) {
    const s = new Set(v);
    if (s.size === 0) return `${name} fingerprints are empty`;
    if (![...s].some((fp) => STRONG.has(fp.split(' ')[0]!))) return `${name} fingerprints hold no sha-256 or stronger`;
    sets[name] = s;
  }
  const sub = (a: Set<string>, b: Set<string>) => [...a].every((x) => b.has(x));
  if (!sub(sets['observed remote']!, sets['declared local']!)) return "the peer's certificate is not the one it signed for";
  if (!sub(sets['declared remote']!, sets['observed local']!)) return 'the peer saw a certificate that is not ours';
  return null;
}

function sortCodePoints(xs: string[]): string[] {
  // fingerprints are ASCII, so code-point order is plain string order
  return [...new Set(xs)].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
}

function fpList(v: unknown): string[] | null {
  if (!Array.isArray(v) || v.length === 0 || v.length > 8) return null;
  for (const x of v) {
    if (typeof x !== 'string') return null;
    const [alg, hex] = [x.slice(0, x.indexOf(' ')), x.slice(x.indexOf(' ') + 1)];
    if (!alg || alg !== alg.toLowerCase() || !FP_NORMAL.test(hex) || x.indexOf(' ') < 0) return null;
  }
  return v as string[];
}

// ---- rooms and the pairing link ---------------------------------------------------------------

export async function pairingId(secret: Uint8Array): Promise<string> {
  checkSecret(secret);
  return toHex(await sha256(utf8('secdogie/pairing-id/v1'), secret)).slice(0, 32);
}

export async function pairingRoom(secret: Uint8Array): Promise<string> {
  checkSecret(secret);
  return `p${b64url(await sha256(utf8('secdogie/pairing-room/v1'), secret)).slice(0, 31)}`;
}

export interface PairingInvite {
  readonly nodeDid: string;
  readonly secret: Uint8Array<ArrayBuffer>;
  readonly expiresAt: number;
  readonly pairingId: string;
  readonly room: string;
}

/**
 * Reads `#pair=...` (with or without the `#`). Throws for anything else --
 * including any field it does not know, so a link that tries to name a
 * signaling address or ICE servers is refused before anything connects.
 */
export async function parsePairingFragment(fragment: string, nowSeconds: number): Promise<PairingInvite> {
  const frag = fragment.startsWith('#') ? fragment.slice(1) : fragment;
  if (!frag.startsWith('pair=') || frag.length > 512) throw new Error('not a pairing link');
  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(b64urlDecode(frag.slice(5))));
  } catch {
    throw new Error('a malformed pairing link');
  }
  if (!isCanonObject(body)) throw new Error('an unknown pairing link');
  const keys = Object.keys(body).sort().join(',');
  if (keys !== 'exp,node,secret,v' || body['v'] !== 1) throw new Error('an unknown pairing link');
  const node = body['node'];
  const exp = body['exp'];
  if (typeof node !== 'string' || !isDidKey(node) || typeof exp !== 'number' || !Number.isSafeInteger(exp)) {
    throw new Error('a malformed pairing link');
  }
  const secret = b64urlDecode(String(body['secret']));
  checkSecret(secret);
  if (exp <= nowSeconds) throw new Error('the pairing link has expired');
  return { nodeDid: node, secret, expiresAt: exp, pairingId: await pairingId(secret), room: await pairingRoom(secret) };
}

// ---- verification helpers ----------------------------------------------------------------------

type Signed = { signer: string } | { signer: null; reason: string; who: string | null };

async function signed(obj: unknown, type: string, trust?: TrustSet): Promise<Signed> {
  if (!isCanonObject(obj) || obj['type'] !== type) return { signer: null, reason: 'wrong or missing type', who: null };
  const v = await verifyPayload(obj, trust);
  if (!v.ok) return { signer: null, reason: v.signer ? 'signer not trusted' : 'invalid or missing signature', who: v.signer };
  if (obj['did'] !== v.signer) return { signer: null, reason: 'did does not match signer', who: null };
  return { signer: v.signer! };
}

function timeOf(v: unknown): number | null {
  if (v instanceof PyFloat) return v.value;
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'bigint') return Number(v);
  return null;
}

function fresh(issuedAt: unknown, now: number): boolean {
  const t = timeOf(issuedAt);
  return t !== null && Math.abs(now - t) <= MAX_SKEW;
}

function checkFps(obj: CanonObject, observedLocal: readonly string[], observedRemote: readonly string[]): string | null {
  const local = fpList(obj['local_fingerprints']);
  const remote = fpList(obj['remote_fingerprints']);
  if (local === null || remote === null) return 'malformed fingerprints';
  return fingerprintsAgree({ declaredLocal: local, declaredRemote: remote, observedLocal, observedRemote });
}

async function hmac(secret: Uint8Array<ArrayBuffer>, core: CanonObject): Promise<string> {
  checkSecret(secret);
  const key = await crypto.subtle.importKey('raw', secret, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return b64url(new Uint8Array(await crypto.subtle.sign('HMAC', key, canonicalBytes(core))));
}

export interface LinkResult {
  readonly ok: boolean;
  readonly reason: string | null;
  readonly did: string | null;
}

// ---- W1 -------------------------------------------------------------------------------------------

export async function createLinkBinding(
  self: Signer,
  o: { room: string; local: readonly string[]; remote: readonly string[]; issuedAt: number },
): Promise<CanonObject> {
  if (!ROOM_PATTERN.test(o.room)) throw new Error('a bad room name');
  return signPayload(self, {
    type: LINK_BINDING_TYPE,
    did: self.did,
    room: o.room,
    local_fingerprints: sortCodePoints([...o.local]),
    remote_fingerprints: sortCodePoints([...o.remote]),
    issued_at: pyFloat(o.issuedAt),
  });
}

export async function verifyLinkBinding(
  obj: unknown,
  o: { trust: TrustSet; room: string; observedLocal: readonly string[]; observedRemote: readonly string[]; now: number },
): Promise<LinkResult> {
  const s = await signed(obj, LINK_BINDING_TYPE, o.trust);
  if (s.signer === null) return { ok: false, reason: s.reason, did: s.who };
  const b = obj as CanonObject;
  if (b['room'] !== o.room) return { ok: false, reason: 'room mismatch', did: s.signer };
  const bad = checkFps(b, o.observedLocal, o.observedRemote);
  if (bad) return { ok: false, reason: bad, did: s.signer };
  if (!fresh(b['issued_at'], o.now)) return { ok: false, reason: 'stale or future statement', did: s.signer };
  return { ok: true, reason: null, did: s.signer };
}

// ---- pairing ----------------------------------------------------------------------------------------

export async function createPairHello(
  app: Signer,
  o: { invite: PairingInvite; local: readonly string[]; remote: readonly string[]; operatorDid: string; issuedAt: number },
): Promise<CanonObject> {
  if (o.operatorDid && (!isDidKey(o.operatorDid) || o.operatorDid === app.did)) throw new Error('a bad operator key');
  const core: CanonObject = {
    type: PAIR_HELLO_TYPE,
    did: app.did,
    node: o.invite.nodeDid,
    room: o.invite.room,
    pairing_id: o.invite.pairingId,
    operator: o.operatorDid,
    local_fingerprints: sortCodePoints([...o.local]),
    remote_fingerprints: sortCodePoints([...o.remote]),
    issued_at: pyFloat(o.issuedAt),
  };
  return signPayload(app, { ...core, mac: await hmac(o.invite.secret, core) });
}

/** The 12 digits both ends show for one signed hello: "4821 0937 5512". */
export async function checkCode(hello: CanonObject): Promise<string> {
  const digest = await sha256(utf8('secdogie/pairing-sas/v1'), canonicalBytes(hello));
  let n = 0n;
  for (const b of digest.subarray(0, 8)) n = (n << 8n) | BigInt(b);
  const s = (n % 10n ** 12n).toString().padStart(12, '0');
  return `${s.slice(0, 4)} ${s.slice(4, 8)} ${s.slice(8, 12)}`;
}

export async function helloHash(hello: CanonObject): Promise<string> {
  return toHex(await sha256(canonicalBytes(hello)));
}

export async function createOperatorProof(
  operator: Signer,
  o: { appDid: string; nodeDid: string; pairingId: string; issuedAt: number },
): Promise<CanonObject> {
  return signPayload(operator, {
    type: PAIR_OPERATOR_TYPE,
    did: operator.did,
    app: o.appDid,
    node: o.nodeDid,
    pairing_id: o.pairingId,
    issued_at: pyFloat(o.issuedAt),
  });
}

export async function createPairConfirm(
  app: Signer,
  o: { invite: PairingInvite; hello: CanonObject; operatorProof: CanonObject | null; issuedAt: number },
): Promise<CanonObject> {
  const core: CanonObject = {
    type: PAIR_CONFIRM_TYPE,
    did: app.did,
    node: o.invite.nodeDid,
    pairing_id: o.invite.pairingId,
    hello: await helloHash(o.hello),
    operator_proof: o.operatorProof,
    issued_at: pyFloat(o.issuedAt),
  };
  return signPayload(app, { ...core, mac: await hmac(o.invite.secret, core) });
}

export interface PairedResult {
  readonly ok: boolean;
  readonly reason: string | null;
  readonly room: string | null;
  readonly operatorDid: string | null;
}

export async function verifyPaired(
  obj: unknown,
  o: { nodeDid: string; appDid: string; pairingId: string; observedLocal: readonly string[]; observedRemote: readonly string[]; now: number },
): Promise<PairedResult> {
  const fail = (reason: string): PairedResult => ({ ok: false, reason, room: null, operatorDid: null });
  const s = await signed(obj, PAIRED_TYPE);
  if (s.signer === null) return fail(s.reason);
  if (s.signer !== o.nodeDid) return fail('signed by another node');
  const p = obj as CanonObject;
  if (p['app'] !== o.appDid || p['pairing_id'] !== o.pairingId) return fail('for another App or pairing');
  const room = p['room'];
  const op = p['operator'];
  if (typeof room !== 'string' || !ROOM_PATTERN.test(room) || typeof op !== 'string') return fail('malformed receipt');
  const bad = checkFps(p, o.observedLocal, o.observedRemote);
  if (bad) return fail(bad);
  if (!fresh(p['issued_at'], o.now)) return fail('stale or future statement');
  return { ok: true, reason: null, room, operatorDid: op || null };
}

export interface RefusalResult {
  readonly ok: boolean;
  readonly reason: string | null;
  readonly refusal: RefusalReason | null;
}

/** Counts only when the expected node signed it for this very link. */
export async function verifyRefusal(
  obj: unknown,
  o: { nodeDid: string; observedLocal: readonly string[]; observedRemote: readonly string[]; now: number },
): Promise<RefusalResult> {
  const fail = (reason: string): RefusalResult => ({ ok: false, reason, refusal: null });
  const s = await signed(obj, REFUSED_TYPE);
  if (s.signer === null) return fail(s.reason);
  if (s.signer !== o.nodeDid) return fail('signed by another node');
  const r = obj as CanonObject;
  const reason = r['reason'];
  if (typeof reason !== 'string' || !(REFUSAL_REASONS as readonly string[]).includes(reason) || typeof r['pairing_id'] !== 'string') {
    return fail('malformed refusal');
  }
  const bad = checkFps(r, o.observedLocal, o.observedRemote);
  if (bad) return fail(bad);
  if (!fresh(r['issued_at'], o.now)) return fail('stale or future statement');
  return { ok: true, reason: null, refusal: reason as RefusalReason };
}

/** A trust set of exactly one DID (the node a page expects). */
export function only(did: string): TrustSet {
  return new TrustSet([did]);
}
