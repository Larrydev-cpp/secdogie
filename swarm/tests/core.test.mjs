import test from "node:test";
import assert from "node:assert/strict";
import {
  AlignmentSession,
  signApproval,
  ProposalQueue,
  StateGraphStore,
  actionHash,
  canonicalJson,
  evaluateAttention,
  sanitizePublicUrl,
} from "../dist/src/index.js";

const ACTION = {
  kind: "delete_file",
  target_id: "file-1",
  target_role: "file",
  target_name: "report.txt",
  text: "delete report.txt",
  high_risk: true,
};

function did(suffix) {
  return `did:key:${suffix}`;
}

test("canonical JSON is deterministic and action hashing is stable", async () => {
  assert.equal(canonicalJson({ b: 2, a: 1 }), '{"a":1,"b":2}');
  assert.equal(await actionHash(ACTION), await actionHash({ ...ACTION }));
});

test("graph merges concurrent append-only deltas and deduplicates", async () => {
  const a = new StateGraphStore("web");
  const b = new StateGraphStore("web");
  const d1 = await a.makeDelta({ vertices: [{ id: "a", route: "/a", sourceHash: "1" }] }, "A", 1);
  const d2 = await b.makeDelta({ vertices: [{ id: "b", route: "/b", sourceHash: "2" }] }, "B", 2);
  assert.equal(await a.applyDelta(d1), "applied");
  assert.equal(await b.applyDelta(d2), "applied");
  assert.equal(await a.applyDelta(d2), "applied");
  assert.equal(await a.applyDelta(d2), "duplicate");
  assert.equal(a.currentEpoch, 1);
});

test("invalid graph deltas are rejected before mutation", async () => {
  const graph = new StateGraphStore("web");
  const delta = await graph.makeDelta({ vertices: [{ id: "x", route: "/x", sourceHash: "h" }] }, "A", 1);
  const forged = { ...delta, addedVertices: [{ ...delta.addedVertices[0], route: "/forged" }] };
  await assert.rejects(() => graph.applyDelta(forged), /invalid state-graph delta hash/);
  assert.equal(graph.currentEpoch, 0);
});

test("public URL sanitization removes query and refuses credentials or unapproved origins", () => {
  const policy = { allowedOrigins: ["https://example.com"], maxBytes: 1000, timeoutMs: 100 };
  const url = sanitizePublicUrl("https://example.com/a?token=secret#x", policy);
  assert.equal(url.toString(), "https://example.com/a");
  assert.throws(() => sanitizePublicUrl("https://user:pass@example.com/a", policy));
  assert.throws(() => sanitizePublicUrl("https://other.example/a", policy));
  assert.throws(() => sanitizePublicUrl("https://127.0.0.1/a", { ...policy, allowedOrigins: ["https://127.0.0.1"] }));
});

test("attention evaluator protects flow and releases only in an idle gap", () => {
  const policy = { flowFocusedMs: 10_000, idleAfterMs: 45_000, minGapMs: 0, cooldownMs: 0, resonanceThreshold: 0.5, maxQueue: 4 };
  assert.equal(evaluateAttention({ observedAtMs: 0, appId: "ide", windowId: "1", phaseHint: "flow", focusedForMs: 20_000, idleForMs: 0, inputEventsPerMinute: 60, contextTags: ["code"], sensitiveContext: false }, policy), "flow");
  assert.equal(evaluateAttention({ observedAtMs: 0, appId: "ide", windowId: "1", phaseHint: "transition", focusedForMs: 0, idleForMs: 60_000, inputEventsPerMinute: 0, contextTags: [], sensitiveContext: false }, policy), "idle");
  const queue = new ProposalQueue(policy);
  const proposal = { proposalId: "p", mutationId: "m", title: "Inspect", detail: "Review", contextTags: ["code"], highRisk: false, createdAtMs: 0 };
  queue.enqueue(proposal);
  const deferred = queue.inspect({ observedAtMs: 1, appId: "ide", windowId: "1", phaseHint: "flow", focusedForMs: 20_000, idleForMs: 0, inputEventsPerMinute: 60, contextTags: ["code"], sensitiveContext: false });
  assert.equal(deferred.disposition, "deferred-flow");
  const ready = queue.inspect({ observedAtMs: 2, appId: "ide", windowId: "1", phaseHint: "transition", focusedForMs: 0, idleForMs: 60_000, inputEventsPerMinute: 0, contextTags: ["code"], sensitiveContext: false });
  assert.equal(ready.disposition, "ready");
});

test("Gate 2 release bytes match the existing Python authz canonical body", async () => {
  const calls = [];
  const verifier = { verify: async (signer, message, signature) => { calls.push({ signer, message, signature }); return signer === did("operator") && signature === "sig"; } };
  let now = 1_000_000;
  const session = new AlignmentSession(did("node"), verifier, { challengeTtlMs: 120_000, clockSkewMs: 30_000 }, () => now);
  const { challenge, bubble } = await session.issue(ACTION, "high", "deletes a file");
  assert.equal(bubble.status, "pending");
  assert.throws(() => session.release(challenge.challengeId), /verified approval/);
  const approved = await session.respond({ challengeId: challenge.challengeId, actionHash: challenge.actionHash, verdict: "Approve", signer: did("operator"), signatureB64: "sig", respondedAtMs: now });
  assert.equal(approved.status, "signed");
  const release = session.release(challenge.challengeId);
  assert.equal(release.actionHash, challenge.actionHash);
  assert.equal(release.authorization.type, "secdogie/action-authorization/v1");
  assert.equal(calls.length, 1);
  const signedWire = new TextDecoder().decode(calls[0].message);
  assert.match(signedWire, /"expires_at":1120\.0/);
  assert.match(signedWire, /"valid_from":1000\.0/);
  const signedBody = JSON.parse(signedWire);
  assert.deepEqual(signedBody, {
    type: "secdogie/action-authorization/v1",
    action_hash: challenge.actionHash,
    subject: did("node"),
    valid_from: challenge.issuedAtMs / 1000,
    expires_at: challenge.expiresAtMs / 1000,
  });
});

test("signApproval produces a one-shot Gate 2 response without node self-signing", async () => {
  let now = 2_000_000;
  const signer = { did: did("operator"), sign: async (message) => `sig:${message.length}` };
  const verifier = { verify: async () => true };
  const session = new AlignmentSession(did("node"), verifier, { challengeTtlMs: 60_000, clockSkewMs: 30_000 }, () => now);
  const { challenge } = await session.issue(ACTION, "irreversible", "exactly one file deletion");
  const response = await signApproval(challenge, signer, () => now);
  assert.equal(response.verdict, "Approve");
  assert.equal(response.signer, did("operator"));
  assert.match(response.signatureB64, /^sig:/);
  await assert.rejects(() => signApproval(challenge, { ...signer, did: did("node") }, () => now), /self-sign/);
});
