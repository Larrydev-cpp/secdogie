/**
 * Gate 2 tokens: `secdogie/action-authorization/v1`, byte-compatible with
 * `citadel/secdogie_citadel/authz.py`.
 *
 * An authorization is one operator's Ed25519 signature over
 * `{type, action_hash, subject, valid_from, expires_at}` -- a one-shot approval
 * of one concrete action, for one node, for a short window. `action_hash`
 * commits to exactly the six fields below, so an approval for "click Save"
 * cannot be replayed to authorize anything else. The times are Python floats
 * on the wire and are signed as such ({@link pyFloat}), so a token minted here
 * verifies in Python and vice versa (fixtures/vectors/gate2.json).
 */

import {
  type CanonObject,
  type CanonValue,
  PyFloat,
  asNumber,
  canonicalBytes,
  isCanonObject,
  pyFloat,
  sha256Hex,
} from '../core/canon.ts';
import type { Signer } from '../core/ed25519.ts';
import { type TrustSet, signPayload, verifyPayload } from '../core/envelope.ts';

export const AUTHORIZATION_TYPE = 'secdogie/action-authorization/v1';

/** The fields `action_hash` commits to -- the v1 action field set, unchanged. */
export const AUTHORIZED_FIELDS = ['kind', 'target_id', 'target_role', 'target_name', 'text', 'high_risk'] as const;

export const DEFAULT_TTL_SECONDS = 300;

/** The six effect-defining fields of an action (Python's `TargetAction`). */
export interface TargetAction {
  readonly kind: string;
  readonly target_id: string;
  readonly target_role: string;
  readonly target_name: string;
  readonly text: string;
  readonly high_risk: boolean;
}

export function targetAction(a: Partial<TargetAction> & { kind: string }): TargetAction {
  return {
    kind: a.kind,
    target_id: a.target_id ?? '',
    target_role: a.target_role ?? '',
    target_name: a.target_name ?? '',
    text: a.text ?? '',
    high_risk: a.high_risk ?? false,
  };
}

export function actionHashInput(action: TargetAction): CanonObject {
  const body: Record<string, CanonValue> = {};
  for (const k of AUTHORIZED_FIELDS) body[k] = action[k];
  return body;
}

/** sha256 hex over the canonical bytes of the six authorized fields. */
export async function actionHash(action: TargetAction): Promise<string> {
  return sha256Hex(canonicalBytes(actionHashInput(action)));
}

export interface AuthorizationWindow {
  /** Seconds since the epoch. Defaults to now. */
  readonly validFrom?: number;
  readonly expiresAt?: number;
  readonly ttlSeconds?: number;
}

/**
 * The operator signs an approval of `action` on node `subjectDid`. The signer
 * is the operator's own key; a node never holds one.
 */
export async function createAuthorization(
  operator: Signer,
  action: TargetAction,
  subjectDid: string,
  window: AuthorizationWindow = {},
  clock: () => number = () => Date.now() / 1000,
): Promise<CanonObject> {
  const vf = window.validFrom ?? clock();
  const exp = window.expiresAt ?? vf + (window.ttlSeconds ?? DEFAULT_TTL_SECONDS);
  if (!(exp > vf)) throw new Error('expires_at must be after valid_from');
  return signPayload(operator, {
    type: AUTHORIZATION_TYPE,
    action_hash: await actionHash(action),
    subject: subjectDid,
    valid_from: pyFloat(vf),
    expires_at: pyFloat(exp),
  });
}

export interface AuthzResult {
  readonly ok: boolean;
  readonly reason: string | null;
  readonly signer: string | null;
  readonly actionHash: string | null;
}

const result = (ok: boolean, reason: string | null, signer: string | null = null, h: string | null = null) =>
  ({ ok, reason, signer, actionHash: h }) satisfies AuthzResult;

/**
 * Whether `token` authorizes `action` on node `subject` now. Same checks, same
 * order, same reasons as Python's `verify_authorization`.
 */
export async function verifyAuthorization(
  token: unknown,
  action: TargetAction,
  opts: { operators: TrustSet | null; subject: string; now?: number; clock?: () => number },
): Promise<AuthzResult> {
  if (!isCanonObject(token)) return result(false, 'not an authorization object');
  if (token['type'] !== AUTHORIZATION_TYPE) return result(false, 'wrong or missing type (domain separation)');
  if (opts.operators === null) return result(false, 'no trusted operators configured');
  const { ok, signer } = await verifyPayload(token, opts.operators);
  if (!ok) {
    return result(false, signer ? 'operator not trusted or revoked' : 'invalid or missing signature', signer);
  }
  const expected = await actionHash(action);
  if (token['action_hash'] !== expected) {
    return result(false, 'token authorizes a different action', signer, expected);
  }
  if (token['subject'] !== opts.subject) return result(false, 'token is for a different node', signer, expected);
  const vf = numeric(token['valid_from']);
  const exp = numeric(token['expires_at']);
  if (vf === null || exp === null) return result(false, 'invalid validity window', signer, expected);
  const t = opts.now ?? (opts.clock ?? (() => Date.now() / 1000))();
  if (t < vf) return result(false, 'authorization not yet valid', signer, expected);
  if (t >= exp) return result(false, 'authorization expired', signer, expected);
  return result(true, null, signer, expected);
}

/** Python's `_is_num`: an int or a float, never a bool. */
function numeric(v: CanonValue | undefined): number | null {
  if (typeof v === 'boolean' || v === undefined) return null;
  if (typeof v === 'number' || typeof v === 'bigint' || v instanceof PyFloat) return asNumber(v);
  return null;
}
