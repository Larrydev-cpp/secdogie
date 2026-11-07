/**
 * Policy bundles, the browser side of `secdogie_policy` (policy/): signed,
 * declarative, tighten-only configuration for a mesh. A bundle is data, never
 * code -- Gate 1 rules, a decision-model configuration, ingestion limits or an
 * allowlist change -- and can only make a gate stricter than the built-in floor.
 *
 * This is a line-for-line mirror of `secdogie_policy.schema` / `.bundle`:
 * the same closed schemas, the same error list (`"<code>:<path>"`, unique,
 * sorted by code point), the same signer rules (one operator for `gate-rules`
 * and `ingest`, `threshold` masters for `decision-model` and `allowlist`). The
 * golden vectors in fixtures/vectors/policy.json hold the two together
 * (tests/policy.test.ts). Change one side and the vectors fail until the other
 * moves with it.
 */

import {
  type CanonObject,
  type CanonValue,
  canonicalBytes,
  canonicalText,
  compareCodePoints,
  isCanonObject,
  sha256Hex,
} from '../core/canon.ts';
import { isDidKey } from '../core/did.ts';
import { base64Decode, verifyEd25519 } from '../core/ed25519.ts';
import type { TrustSet } from '../core/envelope.ts';

export const BUNDLE_TYPE = 'secdogie/policy-bundle/v1';

/** The built-in floor; must equal `secdogie_policy.floor.FLOOR` (checked by the vectors). */
export const FLOOR: Readonly<Record<string, number>> = Object.freeze({
  'instruction.max_len': 600,
  'instruction.max_steps': 6,
  'plan.repeat_threshold': 2,
  'plan.poll_window': 3,
  'plan.known_failure_min_runs': 3,
  'ingest.rps_milli': 1000,
  'ingest.concurrency': 1,
  'ingest.max_body_bytes': 8 << 20,
  'ingest.timeout_ms': 30_000,
});

export const KINDS = ['allowlist', 'decision-model', 'gate-rules', 'ingest'] as const;
export const ROLES = ['broker', 'coordinator', 'desk', 'forward', 'ingest', 'model', 'relay', 'serve'] as const;
export const POLICIES = ['llm/v1', 'router/v1', 'weighted/v1'] as const;
export const STAGES = ['canary', 'full', 'shadow'] as const;
export const MODES = ['desktop-ax', 'rendered', 'session', 'static'] as const;
const OPERATOR_KINDS: ReadonlySet<string> = new Set(['gate-rules', 'ingest']);

const MAX_SIGS = 16;
const MAX_VERSION = 2 ** 31 - 1;
const MAX_NOT_BEFORE = 4_102_444_800;
const TOP_FIELDS = ['body', 'bundle_id', 'kind', 'mesh', 'not_before', 'scope', 'sigs', 'supersedes', 'type', 'version'];

const TOKEN = /^[a-z][a-z0-9_]{0,31}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const MESH = /^[a-z0-9][a-z0-9._-]{0,62}$/;
const LABEL = '[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?';
const ORIGIN = new RegExp(`^https://(${LABEL}(?:\\.${LABEL})+)(?::([1-9][0-9]{0,4}))?$`);
const CONTROL = /[\x00-\x1f\x7f]/;

// ---- phrases ----------------------------------------------------------------------

/** NFKC, then ASCII-only lower case, then single spaces with none at the ends. */
export function normalizePhrase(s: string): string {
  const out = s.normalize('NFKC').replace(/[A-Z]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 32));
  return out.replace(/ +/g, ' ').replace(/^ | $/g, '');
}

export function containsPhrase(text: string, phrase: string): boolean {
  const p = normalizePhrase(phrase);
  return p.length > 0 && normalizePhrase(text).includes(p);
}

// ---- the error list ---------------------------------------------------------------

class Errors {
  readonly #set = new Set<string>();
  add(code: string, path: string): void {
    this.#set.add(`${code}:${path || '$'}`);
  }
  sorted(): string[] {
    return [...this.#set].sort(compareCodePoints);
  }
}

type Obj = CanonObject;
type Item = (v: CanonValue | undefined, path: string, errs: Errors) => boolean;

const join = (path: string, key: string): string => (path ? `${path}.${key}` : key);
const has = (d: Obj, k: string): boolean => Object.hasOwn(d, k);
const len = (s: string): number => [...s].length;

function isInt(v: CanonValue | undefined): v is number | bigint {
  return typeof v === 'bigint' || (typeof v === 'number' && Number.isInteger(v));
}

function obj(v: CanonValue | undefined, path: string, errs: Errors): Obj | null {
  if (!isCanonObject(v)) {
    errs.add('type', path);
    return null;
  }
  return v;
}

function fields(d: Obj, path: string, allowed: readonly string[], required: readonly string[], errs: Errors): void {
  for (const k of Object.keys(d)) if (!allowed.includes(k)) errs.add('unknown-field', join(path, k));
  for (const k of required) if (!has(d, k)) errs.add('missing', join(path, k));
}

function intIn(v: CanonValue | undefined, path: string, lo: number, hi: number, errs: Errors, floor = false): boolean {
  if (!isInt(v)) {
    errs.add('type', path);
    return false;
  }
  const n = typeof v === 'bigint' ? v : BigInt(v);
  if (n < BigInt(lo)) {
    errs.add('range', path);
    return false;
  }
  if (n > BigInt(hi)) {
    errs.add(floor ? 'loosens' : 'range', path);
    return false;
  }
  return true;
}

function trueOnly(v: CanonValue | undefined, path: string, errs: Errors): void {
  if (typeof v !== 'boolean') errs.add('type', path);
  else if (v === false) errs.add('loosens', path);
}

function text(v: CanonValue | undefined, path: string, lo: number, hi: number, errs: Errors): boolean {
  if (typeof v !== 'string') {
    errs.add('type', path);
    return false;
  }
  const n = len(v);
  if (n < lo || n > hi) {
    errs.add('range', path);
    return false;
  }
  if (CONTROL.test(v)) {
    errs.add('format', path);
    return false;
  }
  return true;
}

function enumOf(values: readonly string[]): Item {
  return (v, path, errs) => {
    if (typeof v !== 'string') {
      errs.add('type', path);
      return false;
    }
    if (!values.includes(v)) {
      errs.add('value', path);
      return false;
    }
    return true;
  };
}

function patternOf(rx: RegExp): Item {
  return (v, path, errs) => {
    if (typeof v !== 'string') {
      errs.add('type', path);
      return false;
    }
    if (!rx.test(v)) {
      errs.add('format', path);
      return false;
    }
    return true;
  };
}

const did: Item = (v, path, errs) => {
  if (typeof v !== 'string') {
    errs.add('type', path);
    return false;
  }
  if (!isDidKey(v)) {
    errs.add('format', path);
    return false;
  }
  return true;
};

const origin: Item = (v, path, errs) => {
  if (typeof v !== 'string') {
    errs.add('type', path);
    return false;
  }
  const m = ORIGIN.exec(v);
  let ok = m !== null && m[1]!.length <= 253;
  if (ok && m![2] !== undefined) {
    const port = Number(m![2]);
    ok = port <= 65535 && port !== 443;
  }
  if (!ok) errs.add('format', path);
  return ok;
};

const phrase: Item = (v, path, errs) => {
  if (!text(v, path, 1, 64, errs)) return false;
  const s = v as string;
  if (normalizePhrase(s) !== s || s === '') {
    errs.add('not-normalized', path);
    return false;
  }
  return true;
};

/** Strings each checked by `item`, sorted by code point and duplicate-free. */
function strList(
  v: CanonValue | undefined,
  path: string,
  item: Item,
  errs: Errors,
  lo: number,
  hi: number,
): string[] | null {
  if (!Array.isArray(v)) {
    errs.add('type', path);
    return null;
  }
  const list = v as readonly CanonValue[];
  if (list.length < lo || list.length > hi) {
    errs.add('range', path);
    return null;
  }
  let ok = true;
  list.forEach((x, i) => {
    ok = item(x, `${path}[${i}]`, errs) && ok;
  });
  if (!ok) return null;
  const strs = list as readonly string[];
  const canonical = [...new Set(strs)].sort(compareCodePoints);
  if (canonical.length !== strs.length || canonical.some((s, i) => s !== strs[i])) {
    errs.add('order', path);
    return null;
  }
  return [...strs];
}

function objList(
  v: CanonValue | undefined,
  path: string,
  item: (v: CanonValue | undefined, path: string, errs: Errors) => void,
  errs: Errors,
  hi: number,
): void {
  if (!Array.isArray(v)) {
    errs.add('type', path);
    return;
  }
  const list = v as readonly CanonValue[];
  if (list.length > hi) {
    errs.add('range', path);
    return;
  }
  list.forEach((x, i) => item(x, `${path}[${i}]`, errs));
}

// ---- gate-rules -------------------------------------------------------------------

function reviseOn(v: CanonValue | undefined, path: string, errs: Errors): void {
  const d = obj(v, path, errs);
  if (d === null) return;
  fields(d, path, ['phrases', 'reason', 'suggestion'], ['phrases', 'reason'], errs);
  if (has(d, 'phrases')) strList(d['phrases'], join(path, 'phrases'), phrase, errs, 1, 32);
  if (has(d, 'reason')) text(d['reason'], join(path, 'reason'), 1, 200, errs);
  if (has(d, 'suggestion')) text(d['suggestion'], join(path, 'suggestion'), 0, 200, errs);
}

function blockedTarget(v: CanonValue | undefined, path: string, errs: Errors): void {
  const d = obj(v, path, errs);
  if (d === null) return;
  fields(d, path, ['role', 'name'], [], errs);
  if (!has(d, 'role') && !has(d, 'name')) errs.add('missing', join(path, 'role'));
  for (const k of ['role', 'name']) if (has(d, k)) phrase(d[k], join(path, k), errs);
}

function gateRules(body: Obj, path: string, errs: Errors): void {
  fields(body, path, ['instruction', 'plan'], [], errs);
  if (has(body, 'instruction')) {
    const p = join(path, 'instruction');
    const d = obj(body['instruction'], p, errs);
    if (d !== null) {
      fields(d, p, ['max_len', 'max_steps', 'revise_on'], [], errs);
      if (has(d, 'max_len')) intIn(d['max_len'], join(p, 'max_len'), 1, FLOOR['instruction.max_len']!, errs, true);
      if (has(d, 'max_steps')) {
        intIn(d['max_steps'], join(p, 'max_steps'), 1, FLOOR['instruction.max_steps']!, errs, true);
      }
      if (has(d, 'revise_on')) objList(d['revise_on'], join(p, 'revise_on'), reviseOn, errs, 64);
    }
  }
  if (has(body, 'plan')) {
    const p = join(path, 'plan');
    const d = obj(body['plan'], p, errs);
    if (d !== null) {
      fields(
        d,
        p,
        [
          'require_intent',
          'enforce_capabilities',
          'repeat_threshold',
          'poll_window',
          'known_failure_min_runs',
          'max_cost',
          'extra_destructive_kinds',
          'blocked_kinds',
          'blocked_targets',
        ],
        [],
        errs,
      );
      for (const k of ['require_intent', 'enforce_capabilities']) if (has(d, k)) trueOnly(d[k], join(p, k), errs);
      if (has(d, 'repeat_threshold')) {
        intIn(d['repeat_threshold'], join(p, 'repeat_threshold'), 1, FLOOR['plan.repeat_threshold']!, errs, true);
      }
      if (has(d, 'poll_window')) {
        intIn(d['poll_window'], join(p, 'poll_window'), 2, FLOOR['plan.poll_window']!, errs, true);
      }
      if (has(d, 'known_failure_min_runs')) {
        const hi = FLOOR['plan.known_failure_min_runs']!;
        intIn(d['known_failure_min_runs'], join(p, 'known_failure_min_runs'), 1, hi, errs, true);
      }
      if (has(d, 'max_cost')) intIn(d['max_cost'], join(p, 'max_cost'), 1, 10_000, errs);
      for (const k of ['extra_destructive_kinds', 'blocked_kinds']) {
        if (has(d, k)) strList(d[k], join(p, k), patternOf(TOKEN), errs, 0, 32);
      }
      if (has(d, 'blocked_targets')) objList(d['blocked_targets'], join(p, 'blocked_targets'), blockedTarget, errs, 64);
    }
  }
}

// ---- decision-model ---------------------------------------------------------------

function decisionModel(body: Obj, path: string, errs: Errors): void {
  fields(body, path, ['policy', 'weights', 'deny_providers', 'llm', 'rollout'], ['policy'], errs);
  const policy = body['policy'];
  const known = has(body, 'policy') && enumOf(POLICIES)(policy, join(path, 'policy'), errs);
  if (has(body, 'weights')) {
    const p = join(path, 'weights');
    const d = obj(body['weights'], p, errs);
    if (d !== null) {
      fields(d, p, ['hop_milli', 'load_milli'], [], errs);
      for (const k of ['hop_milli', 'load_milli']) if (has(d, k)) intIn(d[k], join(p, k), 0, 10_000, errs);
    }
  }
  if (has(body, 'deny_providers')) strList(body['deny_providers'], join(path, 'deny_providers'), did, errs, 0, 64);
  if (has(body, 'llm')) {
    const p = join(path, 'llm');
    const d = obj(body['llm'], p, errs);
    if (d !== null) {
      const keys = ['max_tokens', 'model', 'prompt_template_hash', 'timeout_ms'];
      fields(d, p, keys, keys, errs);
      if (has(d, 'model')) text(d['model'], join(p, 'model'), 1, 128, errs);
      if (has(d, 'prompt_template_hash')) {
        patternOf(HEX64)(d['prompt_template_hash'], join(p, 'prompt_template_hash'), errs);
      }
      if (has(d, 'timeout_ms')) intIn(d['timeout_ms'], join(p, 'timeout_ms'), 100, 10_000, errs);
      if (has(d, 'max_tokens')) intIn(d['max_tokens'], join(p, 'max_tokens'), 1, 4096, errs);
    }
  }
  if (has(body, 'rollout')) {
    const p = join(path, 'rollout');
    const d = obj(body['rollout'], p, errs);
    if (d !== null) {
      fields(d, p, ['stage', 'canary', 'promote_after'], ['stage'], errs);
      let stage: string | null = null;
      if (has(d, 'stage') && enumOf(STAGES)(d['stage'], join(p, 'stage'), errs)) stage = d['stage'] as string;
      if (has(d, 'canary')) strList(d['canary'], join(p, 'canary'), did, errs, 1, 8);
      if (has(d, 'promote_after')) intIn(d['promote_after'], join(p, 'promote_after'), 100, 1_000_000, errs);
      if (stage !== null) {
        if (stage === 'canary' && !has(d, 'canary')) errs.add('missing', join(p, 'canary'));
        if (stage !== 'canary' && has(d, 'canary')) errs.add('conflict', join(p, 'canary'));
        if (stage !== 'shadow' && has(d, 'promote_after')) errs.add('conflict', join(p, 'promote_after'));
      }
    }
  }
  if (known) {
    const builtin = policy === 'router/v1';
    if (builtin && has(body, 'weights')) errs.add('conflict', join(path, 'weights'));
    if (policy === 'llm/v1' && !has(body, 'llm')) errs.add('missing', join(path, 'llm'));
    if (policy !== 'llm/v1' && has(body, 'llm')) errs.add('conflict', join(path, 'llm'));
    if (!builtin && !has(body, 'rollout')) errs.add('missing', join(path, 'rollout'));
    if (builtin && has(body, 'rollout')) errs.add('conflict', join(path, 'rollout'));
  }
}

// ---- ingest -----------------------------------------------------------------------

function ingest(body: Obj, path: string, errs: Errors): void {
  fields(
    body,
    path,
    ['origins', 'submitters', 'rate', 'modes', 'max_body_bytes', 'timeout_ms'],
    ['origins', 'submitters'],
    errs,
  );
  if (has(body, 'origins')) strList(body['origins'], join(path, 'origins'), origin, errs, 0, 256);
  if (has(body, 'submitters')) strList(body['submitters'], join(path, 'submitters'), did, errs, 1, 64);
  if (has(body, 'rate')) {
    const p = join(path, 'rate');
    const d = obj(body['rate'], p, errs);
    if (d !== null) {
      fields(d, p, ['rps_milli', 'concurrency'], [], errs);
      if (has(d, 'rps_milli')) intIn(d['rps_milli'], join(p, 'rps_milli'), 1, FLOOR['ingest.rps_milli']!, errs, true);
      if (has(d, 'concurrency')) {
        intIn(d['concurrency'], join(p, 'concurrency'), 1, FLOOR['ingest.concurrency']!, errs, true);
      }
    }
  }
  if (has(body, 'modes')) strList(body['modes'], join(path, 'modes'), enumOf(MODES), errs, 1, 4);
  if (has(body, 'max_body_bytes')) {
    intIn(body['max_body_bytes'], join(path, 'max_body_bytes'), 1, FLOOR['ingest.max_body_bytes']!, errs, true);
  }
  if (has(body, 'timeout_ms')) {
    intIn(body['timeout_ms'], join(path, 'timeout_ms'), 1000, FLOOR['ingest.timeout_ms']!, errs, true);
  }
}

// ---- allowlist --------------------------------------------------------------------

function allowlist(body: Obj, path: string, errs: Errors): void {
  fields(body, path, ['add', 'remove'], [], errs);
  const add = has(body, 'add') ? strList(body['add'], join(path, 'add'), did, errs, 0, 256) : [];
  const remove = has(body, 'remove') ? strList(body['remove'], join(path, 'remove'), did, errs, 0, 256) : [];
  if (add !== null && remove !== null) {
    if (add.length === 0 && remove.length === 0) errs.add('conflict', path);
    else if (add.some((d) => remove.includes(d))) errs.add('conflict', join(path, 'remove'));
  }
}

const BODY: Readonly<Record<string, (body: Obj, path: string, errs: Errors) => void>> = {
  'gate-rules': gateRules,
  'decision-model': decisionModel,
  ingest,
  allowlist,
};

function checkScope(v: CanonValue | undefined, path: string, errs: Errors): void {
  const d = obj(v, path, errs);
  if (d === null) return;
  fields(d, path, ['nodes', 'roles'], ['nodes', 'roles'], errs);
  if (has(d, 'roles')) strList(d['roles'], join(path, 'roles'), enumOf(ROLES), errs, 0, ROLES.length);
  if (has(d, 'nodes')) strList(d['nodes'], join(path, 'nodes'), did, errs, 0, 256);
}

// ---- the bundle -------------------------------------------------------------------

/** The bundle without `bundle_id` and `sigs`: what every signature covers. */
export function unsigned(o: CanonObject): CanonObject {
  const out: Record<string, CanonValue> = Object.create(null);
  for (const [k, v] of Object.entries(o)) if (k !== 'bundle_id' && k !== 'sigs') out[k] = v;
  return out;
}

export async function bundleId(o: CanonObject): Promise<string> {
  return sha256Hex(canonicalText(unsigned(o)));
}

/** Every structural and schema error, sorted. Empty: well formed and tightening only. */
export async function checkBundle(o: unknown): Promise<string[]> {
  const errs = new Errors();
  if (!isCanonObject(o)) {
    errs.add('type', '$');
    return errs.sorted();
  }
  fields(o, '', TOP_FIELDS, TOP_FIELDS, errs);
  if (has(o, 'type') && o['type'] !== BUNDLE_TYPE) errs.add(typeof o['type'] === 'string' ? 'value' : 'type', 'type');
  const kind = o['kind'];
  const kindOk = has(o, 'kind') && enumOf(KINDS)(kind, 'kind', errs);
  if (has(o, 'mesh')) patternOf(MESH)(o['mesh'], 'mesh', errs);
  const versionOk = has(o, 'version') && intIn(o['version'], 'version', 1, MAX_VERSION, errs);
  let supersedesOk = false;
  if (has(o, 'supersedes')) {
    const s = o['supersedes'];
    if (typeof s !== 'string') errs.add('type', 'supersedes');
    else if (s !== '') supersedesOk = patternOf(HEX64)(s, 'supersedes', errs);
    else supersedesOk = true;
  }
  if (versionOk && supersedesOk && (Number(o['version']) === 1) !== (o['supersedes'] === '')) {
    errs.add('conflict', 'supersedes');
  }
  if (has(o, 'scope')) checkScope(o['scope'], 'scope', errs);
  if (has(o, 'not_before')) intIn(o['not_before'], 'not_before', 0, MAX_NOT_BEFORE, errs);
  if (has(o, 'body') && kindOk) {
    const d = obj(o['body'], 'body', errs);
    if (d !== null) BODY[kind as string]!(d, 'body', errs);
  }
  if (has(o, 'bundle_id')) {
    if (typeof o['bundle_id'] !== 'string') errs.add('type', 'bundle_id');
    else if (o['bundle_id'] !== (await bundleId(o))) errs.add('bundle-id', 'bundle_id');
  }
  if (has(o, 'sigs')) {
    const sigs = o['sigs'];
    if (!Array.isArray(sigs)) errs.add('type', 'sigs');
    else if (sigs.length > MAX_SIGS) errs.add('range', 'sigs');
  }
  return errs.sorted();
}

/** The masters that may sign master kinds, and how many must agree. */
export interface Masters {
  readonly dids: TrustSet;
  readonly threshold: number;
}

export interface BundleVerdict {
  readonly ok: boolean;
  readonly errors: readonly string[];
  /** The valid, authorized signers that counted, sorted. */
  readonly signers: readonly string[];
}

async function goodSigners(o: CanonObject, trusted: (did: string) => boolean): Promise<string[]> {
  const message = canonicalBytes(unsigned(o));
  const good: string[] = [];
  for (const entry of (o['sigs'] as readonly CanonValue[] | undefined) ?? []) {
    if (!isCanonObject(entry)) continue;
    const signer = entry['signer'];
    const sigB64 = entry['sig'];
    if (typeof signer !== 'string' || typeof sigB64 !== 'string') continue;
    if (good.includes(signer) || !trusted(signer)) continue;
    let ok = false;
    try {
      ok = await verifyEd25519(signer, message, base64Decode(sigB64));
    } catch {
      ok = false;
    }
    if (ok) good.push(signer);
  }
  return good.sort(compareCodePoints);
}

/**
 * Accept a bundle only if it is well formed, tightens only, and is signed by
 * whom its kind requires. A missing trust set authorizes nothing.
 */
export async function verifyBundle(
  o: unknown,
  trust: { readonly operators?: TrustSet; readonly masters?: Masters } = {},
): Promise<BundleVerdict> {
  const errors = await checkBundle(o);
  if (errors.length > 0) return { ok: false, errors, signers: [] };
  const b = o as CanonObject;
  if (OPERATOR_KINDS.has(b['kind'] as string)) {
    const ops = trust.operators;
    const signers = ops ? await goodSigners(b, (d) => ops.has(d)) : [];
    if (signers.length === 0) return { ok: false, errors: ['unauthorized:sigs'], signers: [] };
    return { ok: true, errors: [], signers };
  }
  const masters = trust.masters;
  if (!masters) return { ok: false, errors: ['unauthorized:sigs'], signers: [] };
  const signers = await goodSigners(b, (d) => masters.dids.has(d));
  if (signers.length < masters.threshold) return { ok: false, errors: ['threshold:sigs'], signers };
  return { ok: true, errors: [], signers };
}

/**
 * Errors in `candidate` as the bundle after `current` (null: none yet). Both
 * are assumed verified. Same kind, mesh and scope; the next version; naming
 * its predecessor.
 */
export function checkSuccessor(current: CanonObject | null, candidate: CanonObject): string[] {
  const errs = new Errors();
  const version = (b: CanonObject): number => Number(b['version'] as number | bigint);
  if (current === null) {
    if (version(candidate) !== 1) errs.add('chain', 'version');
    return errs.sorted();
  }
  for (const k of ['kind', 'mesh', 'scope']) {
    if (canonicalText(candidate[k] ?? null) !== canonicalText(current[k] ?? null)) errs.add('chain', k);
  }
  if (version(candidate) !== version(current) + 1) errs.add('chain', 'version');
  if (candidate['supersedes'] !== current['bundle_id']) errs.add('chain', 'supersedes');
  return errs.sorted();
}
