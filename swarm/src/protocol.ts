/**
 * Shared wire contracts for the Symbiotic Runtime.
 *
 * The contracts deliberately contain no credentials, cookies, pixels, or
 * opaque privileged handles. Large observations are content-addressed; local
 * nodes exchange hashes and structural metadata, then decide locally whether
 * a referenced artifact is in their policy.
 */

export type Did = `did:key:${string}`;
export type HashHex = string;
export type NodeId = string;

export interface PublicFetchTask {
  readonly taskId: string;
  readonly url: string;
  readonly origin: string;
  readonly requestedAtMs: number;
  readonly sourceKind: "html" | "javascript" | "json";
}

export interface RouteEvidence {
  readonly path: string;
  readonly sourceHash: HashHex;
  readonly confidence: number;
  readonly kind: "literal" | "router-pattern";
}

export interface StateVertex {
  readonly id: string;
  readonly route: string;
  readonly sourceHash: HashHex;
}

export interface StateEdge {
  readonly id: string;
  readonly from: string;
  readonly to: string;
  readonly evidence: HashHex;
}

export interface StateGraphSnapshot {
  readonly graphId: string;
  readonly epoch: number;
  readonly parentSnapshotHash: HashHex | null;
  readonly vertices: readonly StateVertex[];
  readonly edges: readonly StateEdge[];
  readonly generatedAtMs: number;
  readonly snapshotHash: HashHex;
}

export interface StateGraphDelta {
  readonly graphId: string;
  readonly epoch: number;
  readonly baseSnapshotHash: HashHex;
  readonly sourceNodeId: NodeId;
  readonly addedVertices: readonly StateVertex[];
  readonly addedEdges: readonly StateEdge[];
  readonly removedVertexIds: readonly string[];
  readonly removedEdgeIds: readonly string[];
  readonly createdAtMs: number;
  readonly deltaHash: HashHex;
}

export interface SwarmHave {
  readonly protocol: "secdogie/swarm-have/v1";
  readonly nodeId: NodeId;
  readonly graphId: string;
  readonly snapshotHash: HashHex | null;
  readonly epoch: number;
}

export interface SwarmWant {
  readonly protocol: "secdogie/swarm-want/v1";
  readonly nodeId: NodeId;
  readonly graphId: string;
  readonly baseSnapshotHash: HashHex | null;
  readonly knownDeltaHashes: readonly HashHex[];
}

export interface SwarmDeltaEnvelope {
  readonly protocol: "secdogie/swarm-delta/v1";
  readonly from: NodeId;
  readonly to: NodeId;
  readonly delta: StateGraphDelta;
}

export type AttentionPhase = "flow" | "transition" | "idle" | "sensitive";

export interface FocusSignal {
  readonly observedAtMs: number;
  readonly appId: string;
  readonly windowId: string;
  readonly phaseHint: AttentionPhase;
  readonly focusedForMs: number;
  readonly idleForMs: number;
  readonly inputEventsPerMinute: number;
  readonly contextTags: readonly string[];
  readonly sensitiveContext: boolean;
}

export interface Proposal {
  readonly proposalId: string;
  readonly mutationId: string;
  readonly title: string;
  readonly detail: string;
  readonly contextTags: readonly string[];
  readonly highRisk: boolean;
  readonly createdAtMs: number;
  readonly actionHash?: HashHex;
}

export type ProposalDisposition = "queued" | "deferred-flow" | "suppressed-sensitive" | "ready" | "expired";

export interface ProposalEvent {
  readonly proposal: Proposal;
  readonly disposition: ProposalDisposition;
  readonly attention: AttentionPhase;
  readonly reason: string;
}

export interface ActionDescriptor {
  readonly kind: string;
  readonly target_id: string;
  readonly target_name: string;
  readonly target_role: string;
  readonly text: string;
  readonly high_risk: boolean;
}

export type RiskLevel = "low" | "high" | "irreversible";
export type Verdict = "Approve" | "Deny";

export interface Gate2Challenge {
  readonly type: "secdogie/action-authorization/v1";
  readonly challengeId: string;
  readonly subjectDid: Did;
  readonly action: ActionDescriptor;
  readonly actionHash: HashHex;
  readonly iskLevel: RiskLevel;
  readonly riskExplanation: string;
  readonly issuedAtMs: number;
  readonly expiresAtMs: number;
  readonly nonce: string;
}

export interface Gate2Response {
  readonly challengeId: string;
  readonly actionHash: HashHex;
  readonly verdict: Verdict;
  readonly signer: Di;
  readonly signatureB64?: string;
  readonly respondedAtMs: number;
}

export interface InlineSignatureBubble {
  readonly challengeId: string;
  readonly label: string;
  readonly actionHashShort: string;
  readonly riskLevel: RiskLevel;
  readonly status: "pending" | "signed" | "denied" | "expired" | "released";
  readonly createdAtMs: number;
}

export interface SignedAuthorization {
  readonly type: "secdogie/action-authorization/v1";
  readonly action_hash: HashHex;
  readonly subject: Did;
  readonly valid_from: number;
  readonly expires_at: number;
  readonly signer: Did;
  readonly sig: string;
}

export interface ReleaseRecord {
  readonly challengeId: string;
  readonly actionHash: HashHex;
  readonly signer: Did;
  readonly signatureB64: string;
  readonly authorization: SignedAuthorization;
  readonly releasedAtMs: number;
}
