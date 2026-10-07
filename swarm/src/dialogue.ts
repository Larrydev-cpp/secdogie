import { canonicalJson, sha256Hex } from "./graph.js";
import type {
  ActionDescriptor,
  Did,
  Gate2Challenge,
  Gate2Response,
  InlineSignatureBubble,
  ReleaseRecord,
  RiskLevel,
  SignedAuthorization,
} from "./protocol.js";

const AUTHORIZATION_TYPE = "secdogie/action-authorization/v1";

export interface Ed25519Signer {
  readonly did: Did;
  sign(message: Uint8Array): Promise<string>;
}

export interface Ed25519Verifier {
  /** The implementation must also enforce the trusted-operator policy. */
  verify(signer: Did, message: Uint8Array, signatureB64: string): Promise<boolean>;
}

export interface AlignmentPolicy {
  readonly challengeTtlMs: number;
  readonly clockSkewMs: number;
}

const DEFAULT_POLICY: AlignmentPolicy = { challengeTtlMs: 120_000, clockSkewMs: 30_000 };

export async function actionHash(action: ActionDescriptor): Promise<string> {
  const committed = {
    kind: action.kind,
    target_id: action.target_id,
    target_role: action.target_role,
    target_name: action.target_name,
    text: action.text,
    high_risk: action.high_risk,
  };
  return sha256Hex(canonicalJson(committed));
}

function pythonCompatibleFloat(value: number): string {
  if (!Number.isFinite(value)) throw new TypeError("authorization timestamps must be finite");
  // Existing secdogie_identity.canonical() serializes Python floats. Gate 2
  // timestamps are floats there, so whole-second values must keep the .0 suffix
  // or the cross-language signature bytes would differ (JS: 1000, Python: 1000.0).
  if (Number.isInteger(value)) return `${value}.0`;
  return JSON.stringify(value);
}

export async function authorizationBytes(auth: Omit<SignedAuthorization, "signer" | "sig">): Promise<Uint8Array> {
  const canonical = `{"action_hash":${JSON.stringify(auth.action_hash)},"expires_at":${pythonCompatibleFloat(auth.expires_at)},"subject":${JSON.stringify(auth.subject)},"type":${JSON.stringify(auth.type)},"valid_from":${pythonCompatibleFloat(auth.valid_from)}}`;
  return new TextEncoder().encode(canonical);
}

export async function signApproval(
  challenge: Gate2Challenge,
  signer: Ed25519Signer,
  clock: () => number = () => Date.now(),
): Promise<Gate2Response> {
  if (signer.did === challenge.subjectDid) throw new Error("the node cannot self-sign its own Gate 2 approval");
  const authorization: Omit<SignedAuthorization, "signer" | "sig"> = {
    type: AUTHORIZATION_TYPE,
    action_hash: challenge.actionHash,
    subject: challenge.subjectDid,
    valid_from: challenge.issuedAtMs / 1000,
    expires_at: challenge.expiresAtMs / 1000,
  };
  if (clock() > challenge.expiresAtMs) throw new Error("challenge expired before signing");
  const signatureB64 = await signer.sign(await authorizationBytes(authorization));
  return {
    challengeId: challenge.challengeId,
    actionHash: challenge.actionHash,
    verdict: "Approve",
    signer: signer.did,
    signatureB64,
    respondedAtMs: clock(),
  };
}

interface PendingChallenge {
  challenge: Gate2Challenge;
  bubble: InlineSignatureBubble;
  response: Gate2Response | null;
  released: ReleaseRecord | null;
}

export class AlignmentSession {
  private readonly pending = new Map<string, PendingChallenge>();
  private nonceCounter = 0;

  constructor(
    private readonly subjectDid: Did,
    private readonly verifier: Ed25519Verifier,
    private readonly policy: AlignmentPolicy = DEFAULT_POLICY,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  async issue(action: ActionDescriptor, riskLevel: RiskLevel, riskExplanation: string): Promise<{ challenge: Gate2Challenge; bubble: InlineSignatureBubble }> {
    const now = this.clock();
    const challengeId = `${now.toString(36)}-${(this.nonceCounter++).toString(36)}`;
    const actionHashValue = await actionHash(action);
    const challenge: Gate2Challenge = {
      type: AUTHORIZATION_TYPE,
      challengeId,
      subjectDid: this.subjectDid,
      action,
      actionHash: actionHashValue,
      riskLevel,
      riskExplanation,
      issuedAtMs: now,
      expiresAtMs: now + this.policy.challengeTtlMs,
      nonce: `${now.toString(36)}-${crypto.getRandomValues(new Uint32Array(2)).join("-")}`,
    };
    const bubble: InlineSignatureBubble = {
      challengeId,
      label: `${action.kind} · ${action.target_name || action.target_id || "target"}`,
      actionHashShort: actionHashValue.slice(0, 16),
      riskLevel,
      status: "pending",
      createdAtMs: now,
    };
    this.pending.set(challengeId, { challenge, bubble, response: null, released: null });
    return { challenge, bubble };
  }

  async respond(response: Gate2Response): Promise<InlineSignatureBubble> {
    const entry = this.pending.get(response.challengeId);
    if (!entry) throw new Error("unknown or already-consumed challenge");
    const now = this.clock();
    if (now < entry.challenge.issuedAtMs - this.policy.clockSkewMs) throw new Error("response is before challenge issuance");
    if (now > entry.challenge.expiresAtMs + this.policy.clockSkewMs) {
      entry.bubble = { ...entry.bubble, status: "expired" };
      throw new Error("challenge expired");
    }
    if (response.actionHash !== entry.challenge.actionHash) throw new Error("action hash mismatch");
    if (response.verdict === "Deny") {
      entry.response = response;
      entry.bubble = { ...entry.bubble, status: "denied" };
      return entry.bubble;
    }
    if (!response.signatureB64) throw new Error("Approve requires an Ed25519 signature");
    if (Math.abs(now - response.respondedAtMs) > this.policy.clockSkewMs) {
      throw new Error("response timestamp is outside the accepted clock-skew window");
    }
    const authorization: Omit<SignedAuthorization, "signer" | "sig"> = {
      type: AUTHORIZATION_TYPE,
      action_hash: entry.challenge.actionHash,
      subject: entry.challenge.subjectDid,
      valid_from: entry.challenge.issuedAtMs / 1000,
      expires_at: entry.challenge.expiresAtMs / 1000,
    };
    const bytes = await authorizationBytes(authorization);
    const valid = await this.verifier.verify(response.signer, bytes, response.signatureB64);
    if (!valid) throw new Error("invalid Ed25519 approval signature");
    entry.response = response;
    entry.bubble = { ...entry.bubble, status: "signed" };
    return entry.bubble;
  }

  release(challengeId: string): ReleaseRecord {
    const entry = this.pending.get(challengeId);
    if (!entry) throw new Error("unknown or already-consumed challenge");
    if (entry.released) return entry.released;
    const now = this.clock();
    if (now > entry.challenge.expiresAtMs) throw new Error("challenge expired before release");
    const response = entry.response;
    if (!response || response.verdict !== "Approve" || !response.signatureB64) {
      throw new Error("release requires a verified approval");
    }
    const authorization: SignedAuthorization = {
      type: AUTHORIZATION_TYPE,
      action_hash: entry.challenge.actionHash,
      subject: entry.challenge.subjectDid,
      valid_from: entry.challenge.issuedAtMs / 1000,
      expires_at: entry.challenge.expiresAtMs / 1000,
      signer: response.signer,
      sig: response.signatureB64,
    };
    const record: ReleaseRecord = {
      challengeId,
      actionHash: entry.challenge.actionHash,
      signer: response.signer,
      signatureB64: response.signatureB64,
      authorization,
      releasedAtMs: now,
    };
    entry.released = record;
    entry.bubble = { ...entry.bubble, status: "released" };
    this.pending.delete(challengeId);
    return record;
  }

  expire(): string[] {
    const now = this.clock();
    const expired: string[] = [];
    for (const [id, entry] of this.pending) {
      if (now > entry.challenge.expiresAtMs) {
        expired.push(id);
        this.pending.delete(id);
      }
    }
    return expired;
  }

  get pendingCount(): number {
    return this.pending.size;
  }
}
