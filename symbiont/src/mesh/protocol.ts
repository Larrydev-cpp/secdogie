/**
 * The inference-mesh wire objects a browser forwarder must check, mirroring
 * `inference/secdogie_inference/protocol.py` (and the admission, incident,
 * reset and lease-revocation rules of `admission.py`, `mutiny.py`, `lease.py`).
 *
 * A browser node is admitted with the forward role only, so it never makes a
 * decision, a lease or an answer. It verifies what it carries and floods, and
 * signs exactly two kinds of object itself: its heartbeats and its rtc-hello.
 * Both must be byte-identical to what the Python reference would sign from the
 * same key and inputs (fixtures/vectors/mesh.json).
 *
 * Numbers follow `core/canon.ts`: a bare `number` is an integer, a float is a
 * {@link PyFloat}, an integer past 2**53 (heartbeat seqs) is a `bigint`.
 */

import {
  type CanonObject,
  type CanonValue,
  PyFloat,
  asBigInt,
  asNumber,
  canonicalBytes,
  canonicalText,
  isCanonObject,
  pyFloat,
  sha256Hex,
} from '../core/canon.ts';
import { isDidKey } from '../core/did.ts';
import { type Signer } from '../core/ed25519.ts';
import { signPayload, verifyPayload } from '../core/envelope.ts';

export const REQUEST = 'secdogie/inference/request/v1';
export const DECISION = 'secdogie/inference/decision/v1';
export const RESPONSE = 'secdogie/inference/response/v1';
export const HEARTBEAT = 'secdogie/inference/heartbeat/v2';
export const ADVISORY = 'secdogie/inference/advisory/v1';
export const FAULT = 'secdogie/inference/fault/v1';
export const INCIDENT = 'secdogie/inference/incident/v1';
export const RESET = 'secdogie/inference/reset/v1';
export const LEASE_REVOCATION = 'secdogie/inference/lease-revocation/v1';
export const ADMISSION = 'secdogie/inference/admission/v1';
export const RTC_HELLO = 'secdogie/inference/rtc-hello/v1';

export const ROLE_SERVE = 'serve';
export const ROLE_FORWARD = 'forward';
export const ROLES: ReadonlySet<string> = new Set(['serve', 'forward', 'model', 'coordinator', 'broker']);

export const MODE_NORMAL = 'normal';
export const MODE_DEGRADED = 'degraded';
export const MODE_LOCKDOWN = 'lockdown';
const MODES: ReadonlySet<string> = new Set([MODE_NORMAL, MODE_DEGRADED, MODE_LOCKDOWN]);

export const VIA_MESH = 'mesh';
export const VIA_CENTRAL = 'central';
const STATUS_OK = 'ok';
const STATUS_REFUSED = 'refused';
const FAULT_REASONS: ReadonlySet<string> = new Set(['timeout', 'invalid', 'divergent']);

export const MAX_INPUT = 8000;
export const MAX_OUTPUT = 8000;
export const MAX_REASON = 200;
export const MAX_MODELS = 4;
export const MAX_QUORUM = 3;
export const MAX_HOPS = 8;
export const MAX_ATTEMPTS = 3;
export const MAX_TTL = 300;
export const MAX_SKEW = 120;
export const MAX_NEIGHBORS = 32;
export const MAX_LEASES = 4;
export const GENESIS = '0'.repeat(64);
export const HEAD = 32;
export const MAX_ADMISSION_TTL = 30 * 86400;
const MAX_REVOKED = 64;
const MAX_TARGETS = 16;

export const GRANTABLE_SCOPES: ReadonlySet<string> = new Set([
  'observe.read', 'physical.click', 'physical.type', 'physical.key', 'physical.scroll', 'physical.drag',
  'system.open', 'process.run', 'network.post', 'network.send',
]);

export const INCIDENT_KINDS: ReadonlySet<string> = new Set([
  'provider.equivocation', 'provider.lease-violation', 'provider.secret-exfiltration',
  'model.forbidden-capability', 'model.self-reported', 'decider.tampering', 'decider.self-reported',
  'node.heartbeat-fork',
]);
export const ISOLATE_ONLY: ReadonlySet<string> = new Set(['node.heartbeat-fork']);

export const FRAME_KINDS: ReadonlySet<string> = new Set([
  'submit', 'req', 'resp', 'nack', 'result', 'refused', 'flood', 'grant', 'dir?', 'dir', 'inc?',
]);

const MODEL = /^[A-Za-z0-9._:/@+-]{1,128}$/;
const HEX32 = /^[0-9a-f]{32}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const HEAD_RE = /^[0-9a-f]{32}$/;
const FINGERPRINT = /^a=fingerprint:(\S+)\s+([0-9A-Fa-f:]+)\s*$/m;

// -- trust ------------------------------------------------------------------------------

/** Anything with a `has(did)`: a {@link DidSet}, an admission book, a union. */
export interface Trusts {
  has(did: string): boolean;
}

/**
 * A set of trusted DIDs. Unlike `core/envelope.ts`'s `TrustSet` it may be
 * empty -- an empty list here simply trusts nobody for that role (a mesh
 * without a coordinator has no coordinators).
 */
export class DidSet implements Trusts {
  readonly #dids: ReadonlySet<string>;

  constructor(dids: Iterable<string> = []) {
    const set = new Set<string>();
    for (const d of dids) {
      if (!isDidKey(d)) throw new Error(`not an Ed25519 did:key: ${d}`);
      set.add(d);
    }
    this.#dids = set;
  }

  has(did: string): boolean {
    return this.#dids.has(did);
  }

  get dids(): readonly string[] {
    return [...this.#dids];
  }
}

export const anyOf = (...sets: Trusts[]): Trusts => ({ has: (d) => sets.some((s) => s.has(d)) });

/** `TrustRoots` from protocol.py, for a browser: plain DID lists from its join bundle. */
export interface MeshTrust {
  readonly mesh: Trusts;
  readonly users: Trusts;
  readonly deciders: Trusts;
  readonly coordinators: Trusts;
  readonly brokers: Trusts;
  readonly operators: Trusts;
}

export const peersOf = (t: MeshTrust): Trusts => anyOf(t.mesh, t.users);

// -- helpers ----------------------------------------------------------------------------

export async function digest(obj: CanonValue): Promise<string> {
  return sha256Hex(canonicalBytes(obj));
}

export const isDid = (v: unknown): v is string => typeof v === 'string' && isDidKey(v);
export const isHex64 = (v: unknown): v is string => typeof v === 'string' && HEX64.test(v);
export const isHex32 = (v: unknown): v is string => typeof v === 'string' && HEX32.test(v);
export const isModel = (v: unknown): v is string => typeof v === 'string' && MODEL.test(v);
export const isObj = (v: unknown): v is CanonObject => isCanonObject(v);
export const isInt = (v: unknown): boolean =>
  (typeof v === 'number' && Number.isSafeInteger(v)) || typeof v === 'bigint';
export const isNum = (v: unknown): boolean => v instanceof PyFloat || isInt(v);
/** A numeric field as a JS number (seconds, loads), or NaN. */
export const num = (v: CanonValue | undefined): number => asNumber(v) ?? Number.NaN;
const str = (v: CanonValue | undefined): string | null => (typeof v === 'string' ? v : null);
const arr = (v: CanonValue | undefined): readonly CanonValue[] | null => (Array.isArray(v) ? v : null);

function sortedUnique(list: readonly string[]): boolean {
  for (let i = 1; i < list.length; i++) if (!(list[i - 1]! < list[i]!)) return false;
  return true;
}

function stringList(v: CanonValue | undefined): string[] | null {
  const a = arr(v);
  if (a === null || !a.every((x) => typeof x === 'string')) return null;
  return a as string[];
}

/**
 * Null when `obj` is a `type` object validly signed by a DID on `trust` that
 * equals `obj[field]`; otherwise why not (`protocol.signed_by`).
 */
export async function signedBy(obj: unknown, type: string, field: string, trust: Trusts): Promise<string | null> {
  if (!isObj(obj) || obj['type'] !== type) return 'wrong or missing type';
  const v = await verifyPayload(obj);
  if (!v.ok || v.signer === null) return 'invalid or missing signature';
  if (!trust.has(v.signer)) return 'signer not trusted';
  if (obj[field] !== v.signer) return `${field} is not the signer`;
  return null;
}

// -- request / decision / response -------------------------------------------------------

export function requestShape(o: CanonObject): string | null {
  if (!isHex32(o['req_id'])) return 'bad req_id';
  const models = stringList(o['models']);
  if (models === null || models.length < 1 || models.length > MAX_MODELS) return `a request names 1..${MAX_MODELS} models`;
  if (!models.every(isModel) || !sortedUnique(models)) return 'bad model list';
  const input = str(o['input']);
  if (input === null || input.length > MAX_INPUT) return 'input missing or too long';
  const scopes = stringList(o['scopes']);
  if (scopes === null || !sortedUnique(scopes)) return 'bad scopes';
  if (scopes.some((s) => !GRANTABLE_SCOPES.has(s))) return 'a scope is not grantable';
  const quorum = o['quorum'];
  if (typeof quorum !== 'number' || !Number.isSafeInteger(quorum) || quorum < 1 || quorum > MAX_QUORUM) {
    return `quorum must be 1..${MAX_QUORUM}`;
  }
  const pins = arr(o['pins']);
  if (pins === null || pins.length > MAX_QUORUM) return 'bad pins';
  const pinned = new Set<string>();
  for (const pin of pins) {
    const p = arr(pin);
    if (p === null || p.length !== 2 || !isDid(p[0]) || !models.includes(p[1] as string)) {
      return "a pin must be [provider DID, one of the request's models]";
    }
    if (pinned.has(p[0] as string)) return 'a provider is pinned twice';
    pinned.add(p[0] as string);
  }
  if (pins.length > 0 && pins.length < quorum) return 'fewer pins than the quorum';
  const iss = num(o['issued_at']);
  const exp = num(o['expires_at']);
  if (!isNum(o['issued_at']) || !isNum(o['expires_at']) || !(exp - iss > 0 && exp - iss <= MAX_TTL)) {
    return 'bad validity window';
  }
  return null;
}

export async function checkRequest(obj: unknown, trust: MeshTrust, now: number): Promise<string | null> {
  const reason = (await signedBy(obj, REQUEST, 'user', trust.users)) ?? requestShape(obj as CanonObject);
  if (reason) return reason;
  const o = obj as CanonObject;
  if (num(o['issued_at']) > now + MAX_SKEW) return 'issued in the future';
  if (now >= num(o['expires_at'])) return 'expired';
  return null;
}

export function decisionShape(o: CanonObject, trust: MeshTrust): string | null {
  if (!MODES.has(o['mode'] as string)) return 'unknown mode';
  if (!isHex64(o['request_hash'])) return 'bad request_hash';
  const epoch = o['epoch'];
  if (!isInt(epoch) || (asBigInt(epoch) ?? -1n) < 0n) return 'bad epoch';
  const attempt = o['attempt'];
  if (typeof attempt !== 'number' || !Number.isSafeInteger(attempt) || attempt < 0 || attempt >= MAX_ATTEMPTS) {
    return 'bad attempt';
  }
  if (!isNum(o['issued_at'])) return 'bad issued_at';
  const legs = arr(o['legs']);
  if (legs === null || legs.length < 1 || legs.length > MAX_QUORUM) return `a decision has 1..${MAX_QUORUM} legs`;
  const providers = new Set<string>();
  for (const leg of legs) {
    if (!isObj(leg) || !isDid(leg['provider']) || !isModel(leg['model'])) return 'bad leg';
    const path = stringList(leg['path']);
    if (path === null || path.length < 2 || path.length > MAX_HOPS || !path.every(isDid)) return 'bad leg path';
    if (new Set(path).size !== path.length) return 'a leg path visits a node twice';
    if (path[0] !== o['decider'] || path[path.length - 1] !== leg['provider']) {
      return 'a leg path must run from the decider to its provider';
    }
    if (leg['via'] === VIA_CENTRAL) {
      if (path.length !== 3 || !trust.coordinators.has(path[1]!)) return 'a central leg is decider -> coordinator -> provider';
    } else if (leg['via'] === VIA_MESH) {
      if (path.slice(1, -1).some((d) => trust.coordinators.has(d))) return 'a mesh leg does not pass through the coordinator';
    } else {
      return 'unknown via';
    }
    if (providers.has(leg['provider'])) return 'a provider appears on two legs';
    providers.add(leg['provider']);
  }
  return null;
}

export async function checkDecision(obj: unknown, trust: MeshTrust, request?: CanonObject): Promise<string | null> {
  const mode = isObj(obj) ? obj['mode'] : null;
  const signers = mode === MODE_LOCKDOWN ? trust.mesh : trust.deciders;
  const reason = (await signedBy(obj, DECISION, 'decider', signers)) ?? decisionShape(obj as CanonObject, trust);
  if (reason) return reason;
  if (request !== undefined && (obj as CanonObject)['request_hash'] !== (await digest(request))) {
    return 'decision is for a different request';
  }
  return null;
}

export function responseShape(o: CanonObject): string | null {
  if (!isHex64(o['request_hash']) || !isHex64(o['decision_hash'])) return 'bad hashes';
  if (!isModel(o['model']) || !isObj(o['lease'])) return 'bad model or lease';
  if (o['status'] !== STATUS_OK && o['status'] !== STATUS_REFUSED) return 'bad status';
  const output = str(o['output']);
  const reason = str(o['reason']);
  if (output === null || output.length > MAX_OUTPUT) return 'output missing or too long';
  if (o['status'] === STATUS_REFUSED && output) return 'a refusal carries no output';
  if (reason === null || reason.length > MAX_REASON) return 'bad reason';
  if (!isNum(o['issued_at'])) return 'bad issued_at';
  return null;
}

export async function checkResponse(obj: unknown, trust: MeshTrust, decision: CanonObject): Promise<string | null> {
  const reason = (await signedBy(obj, RESPONSE, 'provider', trust.mesh)) ?? responseShape(obj as CanonObject);
  if (reason) return reason;
  const o = obj as CanonObject;
  if (o['decision_hash'] !== (await digest(decision)) || o['request_hash'] !== decision['request_hash']) {
    return 'response is for a different decision';
  }
  const legs = (decision['legs'] as CanonObject[]) ?? [];
  if (!legs.some((leg) => leg['provider'] === o['provider'] && leg['model'] === o['model'])) {
    return 'the decision gave this provider no leg for this model';
  }
  return null;
}

// -- heartbeat ------------------------------------------------------------------------------

/** One witness entry: the newest heartbeat heard directly from a neighbour. */
export interface Witness {
  readonly seq: bigint;
  readonly head: string;
}

export interface HeartbeatInputs {
  readonly seq: bigint;
  readonly prev: string;
  /** neighbour -> the newest heartbeat it handed us itself (seq 0n, head '' if none yet) */
  readonly neighbors: ReadonlyMap<string, Witness>;
  readonly issuedAt: number;
  readonly load?: number;
  readonly ledgerRoot?: string;
  readonly roles?: readonly string[];
}

const intValue = (n: bigint): number | bigint => (n <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(n) : n);

/** `protocol.make_heartbeat`, forward role, no leases: the same bytes Python signs. */
export async function makeHeartbeat(signer: Signer, h: HeartbeatInputs): Promise<CanonObject> {
  const listed = [...h.neighbors.keys()].filter((d) => d !== signer.did).sort().slice(0, MAX_NEIGHBORS);
  const body: CanonObject = {
    type: HEARTBEAT,
    node: signer.did,
    roles: [...new Set(h.roles ?? [ROLE_FORWARD])].sort(),
    seq: intValue(h.seq),
    prev: h.prev,
    neighbors: listed.map((d) => {
      const w = h.neighbors.get(d)!;
      return [d, intValue(w.seq), w.head.slice(0, HEAD)];
    }),
    load: pyFloat(Math.min(Math.max(h.load ?? 0, 0), 1)),
    leases: [],
    ledger_root: h.ledgerRoot ?? GENESIS,
    issued_at: pyFloat(h.issuedAt),
  };
  return signPayload(signer, body);
}

function witnessList(v: CanonValue | undefined): boolean {
  const a = arr(v);
  if (a === null || a.length > MAX_NEIGHBORS) return false;
  const dids: string[] = [];
  for (const entry of a) {
    const e = arr(entry);
    if (e === null || e.length !== 3 || !isDid(e[0]) || !isInt(e[1]) || typeof e[2] !== 'string') return false;
    const seq = asBigInt(e[1])!;
    if (seq < 0n) return false;
    if (e[2] && !HEAD_RE.test(e[2])) return false;
    if ((seq === 0n) !== (e[2] === '')) return false;
    dids.push(e[0]);
  }
  return sortedUnique(dids);
}

export async function checkHeartbeat(obj: unknown, trust: MeshTrust, now?: number): Promise<string | null> {
  const reason = await signedBy(obj, HEARTBEAT, 'node', trust.mesh);
  if (reason) return reason;
  const o = obj as CanonObject;
  const roles = stringList(o['roles']);
  if (roles === null || !sortedUnique(roles) || !roles.every((r) => ROLES.has(r))) return 'bad roles';
  const seq = asBigInt(o['seq']);
  if (!isInt(o['seq']) || seq === null || seq < 0n) return 'bad seq';
  if (!isHex64(o['prev'])) return 'bad prev';
  if (!witnessList(o['neighbors'])) return 'bad neighbours';
  if ((o['neighbors'] as CanonValue[][]).some((e) => e[0] === o['node'])) return 'bad neighbours';
  if (!isHex64(o['ledger_root'])) return 'bad ledger_root';
  const load = num(o['load']);
  if (!isNum(o['load']) || !(load >= 0 && load <= 1)) return 'bad load';
  const leases = arr(o['leases']);
  if (leases === null || leases.length > MAX_LEASES || leases.some((x) => !isObj(x))) return 'bad leases';
  if (!isNum(o['issued_at'])) return 'bad issued_at';
  if (now !== undefined && num(o['issued_at']) > now + MAX_SKEW) return 'issued in the future';
  return null;
}

// -- the other flooded objects ----------------------------------------------------------------

export async function checkAdvisory(obj: unknown, trust: MeshTrust, now: number): Promise<string | null> {
  const reason = await signedBy(obj, ADVISORY, 'coordinator', trust.coordinators);
  if (reason) return reason;
  const o = obj as CanonObject;
  if (!isInt(o['epoch']) || (asBigInt(o['epoch']) ?? -1n) < 0n) return 'bad epoch';
  for (const key of ['failed', 'quarantined', 'bypass', 'recruit']) if (arr(o[key]) === null) return `bad ${key} list`;
  if (!isNum(o['issued_at']) || num(o['issued_at']) > now + MAX_SKEW) return 'bad issued_at';
  return null;
}

export async function checkFault(obj: unknown, trust: MeshTrust, now: number): Promise<string | null> {
  const reason = await signedBy(obj, FAULT, 'reporter', trust.mesh);
  if (reason) return reason;
  const o = obj as CanonObject;
  if (!isDid(o['accused']) || o['accused'] === o['reporter'] || !trust.mesh.has(o['accused'])) return 'bad accused';
  if (!FAULT_REASONS.has(o['reason'] as string)) return 'unknown reason';
  if (o['request_hash'] !== '' && !isHex64(o['request_hash'])) return 'bad request_hash';
  if (!isNum(o['issued_at']) || Math.abs(num(o['issued_at']) - now) > MAX_SKEW) return 'stale or future report';
  return null;
}

export async function checkLeaseRevocation(obj: unknown, trust: MeshTrust): Promise<string | null> {
  const reason = await signedBy(obj, LEASE_REVOCATION, 'broker', trust.brokers);
  if (reason) return reason;
  const o = obj as CanonObject;
  const ids = stringList(o['lease_ids']);
  if (ids === null || ids.length < 1 || ids.length > MAX_REVOKED || !sortedUnique(ids) || !ids.every(isHex32)) {
    return 'bad lease_ids';
  }
  if (typeof o['reason'] !== 'string' || !isNum(o['issued_at'])) return 'bad revocation';
  return null;
}

export function admissionShape(o: CanonObject): string | null {
  if (!isDid(o['subject']) || o['subject'] === o['operator']) return 'bad subject';
  const roles = stringList(o['roles']);
  if (roles === null || roles.length === 0 || !sortedUnique(roles) || roles.some((r) => r !== ROLE_FORWARD)) {
    return 'an admission grants the forward role only';
  }
  if (!isNum(o['issued_at']) || !isNum(o['expires_at']) || !(num(o['issued_at']) < num(o['expires_at']))) {
    return 'bad validity window';
  }
  if (num(o['expires_at']) - num(o['issued_at']) > MAX_ADMISSION_TTL) return 'admission longer than MAX_ADMISSION_TTL';
  return null;
}

export async function checkAdmission(obj: unknown, trust: MeshTrust, now: number): Promise<string | null> {
  const reason = (await signedBy(obj, ADMISSION, 'operator', trust.operators)) ?? admissionShape(obj as CanonObject);
  if (reason) return reason;
  const o = obj as CanonObject;
  if (num(o['issued_at']) > now + MAX_SKEW) return 'issued in the future';
  if (num(o['expires_at']) <= now) return 'expired';
  return null;
}

/**
 * An incident a browser can hold: signed by a mesh node or user, a known kind,
 * and its id the digest of its kind and evidence. A browser does not re-run the
 * evidence verifiers of `mutiny.py` (they need the full trust state and the
 * screens); it reads the targets the evidence names. Since the signer must be
 * trusted, the browser's view is a conservative superset of a full node's: it
 * may refuse more, never less.
 */
export interface HeldIncident {
  readonly id: string;
  readonly kind: string;
  readonly targets: ReadonlySet<string>;
  readonly signed: CanonObject;
}

function namedTargets(kind: string, ev: CanonObject, reporter: string): string[] {
  const o = (v: CanonValue | undefined): CanonObject => (isObj(v) ? v : (Object.create(null) as CanonObject));
  const did = (v: CanonValue | undefined): string | null => (isDid(v) ? v : null);
  const model = (v: CanonValue | undefined): string | null => (isModel(v) ? v : null);
  const out: (string | null)[] = [];
  switch (kind) {
    case 'provider.equivocation': {
      const p = did(o(ev['a'])['provider']);
      out.push(p && `node:${p}`);
      break;
    }
    case 'provider.lease-violation':
    case 'provider.secret-exfiltration': {
      const p = did(o(ev['response'])['provider']);
      out.push(p && `node:${p}`);
      break;
    }
    case 'model.forbidden-capability': {
      const r = o(ev['response']);
      const m = model(r['model']);
      const p = did(r['provider']);
      out.push(m && `model:${m}`, p && `node:${p}`);
      break;
    }
    case 'model.self-reported': {
      const m = model(o(ev['lease'])['model']);
      out.push(m && `model:${m}`);
      break;
    }
    case 'decider.tampering': {
      const d = did(o(ev['decision'])['decider']);
      out.push(d && `decider:${d}`);
      break;
    }
    case 'decider.self-reported':
      out.push(`decider:${reporter}`);
      break;
    case 'node.heartbeat-fork': {
      const n = did(o(ev['a'])['node']);
      out.push(n && `node:${n}`);
      break;
    }
  }
  return out.filter((t): t is string => t !== null);
}

export async function readIncident(obj: unknown, trust: MeshTrust): Promise<HeldIncident | null> {
  if (await signedBy(obj, INCIDENT, 'reporter', peersOf(trust))) return null;
  const o = obj as CanonObject;
  const kind = o['kind'];
  const evidence = o['evidence'];
  if (typeof kind !== 'string' || !INCIDENT_KINDS.has(kind) || !isObj(evidence) || !isNum(o['issued_at'])) return null;
  if (o['incident_id'] !== (await digest({ kind, evidence }))) return null;
  const targets = new Set(namedTargets(kind, evidence, o['reporter'] as string));
  if (targets.size === 0) return null;
  return { id: o['incident_id'] as string, kind, targets, signed: o };
}

/** An operator reset's `(targets, incident ids)`, or null (`mutiny.verify_reset`). */
export async function readReset(obj: unknown, trust: MeshTrust): Promise<[string[], string[]] | null> {
  if (await signedBy(obj, RESET, 'operator', trust.operators)) return null;
  const o = obj as CanonObject;
  const targets = stringList(o['targets']);
  const ids = stringList(o['incident_ids']);
  if (targets === null || targets.length < 1 || targets.length > MAX_TARGETS) return null;
  if (targets.some((t) => !t.includes(':')) || !sortedUnique(targets)) return null;
  if (ids === null || ids.length === 0 || !ids.every(isHex64) || !sortedUnique(ids)) return null;
  if (!isNum(o['issued_at'])) return null;
  return [targets, ids];
}

// -- Merkle roots (merkle.py, RFC 6962 style) -------------------------------------------------------

async function sha256(bytes: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
}

const prefixed = (tag: number, ...parts: Uint8Array[]): Uint8Array<ArrayBuffer> => {
  const out = new Uint8Array(1 + parts.reduce((n, p) => n + p.length, 0));
  out[0] = tag;
  let at = 1;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
};

async function subtree(hashes: Uint8Array[]): Promise<Uint8Array> {
  if (hashes.length === 1) return hashes[0]!;
  let k = 1;
  while (k * 2 < hashes.length) k *= 2; // the largest power of two below n
  return sha256(prefixed(1, await subtree(hashes.slice(0, k)), await subtree(hashes.slice(k))));
}

/** Hex root over string leaves in the order given; 64 zeros for none (`merkle.merkle_root`). */
export async function merkleRoot(leaves: readonly string[]): Promise<string> {
  if (leaves.length === 0) return GENESIS;
  const enc = new TextEncoder();
  const hashes = await Promise.all(leaves.map((l) => sha256(prefixed(0, enc.encode(l)))));
  return [...(await subtree(hashes))].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// -- the WebRTC binding ----------------------------------------------------------------------------

/** The signaling room where `did` waits for peers (`rtc.lobby_room`). */
export async function lobbyRoom(did: string, salt = ''): Promise<string> {
  return 'sdl-' + (await sha256Hex(`lobby|${did}|${salt}`)).slice(0, 40);
}

/** `algorithm HEX:HEX...` from an SDP's first `a=fingerprint` line, normalized. */
export function sdpFingerprint(sdp: string): string {
  const m = FINGERPRINT.exec(sdp ?? '');
  if (m === null) throw new Error('SDP without a DTLS fingerprint');
  return `${m[1]!.toLowerCase()} ${m[2]!.toUpperCase()}`;
}

export async function makeHello(
  signer: Signer,
  h: { room: string; localFp: string; remoteFp: string; issuedAt: number; admission?: CanonObject },
): Promise<CanonObject> {
  const body: Record<string, CanonValue> = {
    type: RTC_HELLO, did: signer.did, room: h.room, local_fp: h.localFp, remote_fp: h.remoteFp,
    issued_at: pyFloat(h.issuedAt),
  };
  if (h.admission !== undefined) body['admission'] = h.admission;
  return signPayload(signer, body);
}

/** Verify a peer's hello on this connection; the peer's DID, or throws (`rtc.check_hello`). */
export async function checkHello(
  obj: unknown,
  trust: MeshTrust,
  c: { room: string; myFp: string; peerFp: string; now: number },
): Promise<string> {
  if (!isObj(obj) || obj['type'] !== RTC_HELLO) throw new Error('not an rtc-hello');
  const v = await verifyPayload(obj);
  const did = obj['did'];
  if (!v.ok || !isDid(did) || v.signer !== did) throw new Error('bad hello signature');
  if (obj['room'] !== c.room) throw new Error('hello for another room');
  if (obj['local_fp'] !== c.peerFp || obj['remote_fp'] !== c.myFp) {
    throw new Error("hello does not match this connection's DTLS fingerprints");
  }
  if (!isNum(obj['issued_at']) || Math.abs(num(obj['issued_at']) - c.now) > MAX_SKEW) throw new Error('stale or future hello');
  // A browser dials only infrastructure nodes named in its bundle: no admissions accepted here.
  if (!trust.mesh.has(did)) throw new Error('peer not trusted');
  return did;
}

// -- frames ----------------------------------------------------------------------------------------

export type Frame = CanonObject & { readonly k: string };

/** A frame as text. Canonical JSON is valid JSON with the int/float distinction Python keeps. */
export function encodeFrame(kind: string, fields: Record<string, CanonValue> = {}): string {
  if (!FRAME_KINDS.has(kind)) throw new Error(`unknown frame kind ${kind}`);
  return canonicalText({ k: kind, ...fields });
}
