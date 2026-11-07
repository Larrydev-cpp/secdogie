# ADR-0001 · "Decentralized data ingestion" is inference-request routing, not a peer web-scraping swarm

Status: **accepted** · Scope: `inference/`, `symbiont/src/sandbox/`, `agent/`, `native/atlas`

This record settles what the "decentralized data ingestion & extraction layer"
means in secdogie, maps the operator's three-state diagram onto the code that
already exists, and specifies the protocol interfaces for the three deliverables
that were asked for — each kept inside the trust model in [`../SECURITY.md`](../SECURITY.md).

It exists because a refactor brief proposed inverting two documented security
invariants (below). Those invariants are not regressions; they are the point of
the project. The brief's third ask — three-state fallback routing — is real,
legitimate, and already built. This ADR adopts the third and declines the first
two, and writes down why so the next person does not re-open it.

---

## 1. The decision

### 1.1 What was requested

1. **Distributed DOM/web extraction swarm** — peer nodes "NOT restricted to
   isolated local evaluation," performing "remote web page fetching, DOM tree
   extraction… on behalf of the orchestration mesh," with "heavy data retrieval
   … distributed across the idle P2P peer mesh to prevent local host
   bottlenecks."
2. **Generalized in-memory host/web perception** — reading "external web
   targets and native host windows" via AX/UIA + "memory-mapped data structure
   parsing," framed as a capability of the mesh at large.
3. **Three-state fallback routing** — normal topology, node-failure isolation,
   and a safety circuit-breaker on adversarial upstream behaviour.

### 1.2 What is adopted

| Ask | Decision | Why |
| --- | --- | --- |
| 1 — offloaded external-fetch swarm | **Declined** | Inverts the sandbox fetch boundary and the owned-machine boundary (§1.3). The mesh delegates **inference** tasks, not third-party web retrieval. |
| 2 — generalized host/web perception | **Bounded, not generalized** | Structure-first perception already exists and stays **read-only, on machines the operator owns** (§4). It is not re-scoped to "the mesh" or to targets the operator does not control. |
| 3 — three-state fallback routing | **Adopted** | Already implemented in `inference/`. This ADR is its consolidated interface spec (§3, §5). |

### 1.3 The two invariants that do not move

These are load-bearing security controls, documented and tested, not accidental
restrictions:

- **The sandbox fetch boundary.** `symbiont/src/sandbox/policy.ts` fetches only
  over HTTPS, only to an **explicit origin allowlist** (empty ⇒ fetch nothing),
  with **no credentials / cookies / referrer**, no redirect-following, and a
  bounded body/time. `SECURITY.md` lists a fetch that *leaves* this allowlist as
  a **reportable vulnerability**. A design in which idle peers fetch arbitrary
  external targets for one another is exactly that boundary removed, fanned out
  across hosts so no single origin is attributable — a scraping/proxy swarm. Not
  built.
- **The owned-machine boundary.** secdogie is "one operator driving machines
  they own or are explicitly authorized to control" (`SECURITY.md`, Trust
  Model). Running it against machines/accounts the operator does not control is
  listed as **out of scope / misuse**. Perception and action stay on the
  operator's own authenticated devices, under capability authorization + HITL.

Nothing below weakens either one. Where the three-state routing carries a
user's *input* across forwarders, the honest limit (forwarders see the prompt;
end-to-end sealing to the model node is not yet built) is recorded in
[`../inference/README.md`](../inference/README.md) and unchanged here.

---

## 2. The diagram, decoded

The operator's sketch is an **inference routing mesh**: a user's signed request
reaches a model-API node that holds a short key lease, and the signed answer
returns. Every drawn box already has a class.

| Drawn | Code (`secdogie_inference`) | Role const |
| --- | --- | --- |
| `central server` | `Coordinator` — signed advisories, repair plans, central fallback leg. *Not a trust root.* | `ROLE_COORDINATOR` |
| `API key` | `KeyBroker` — lends upstream credentials as short leases, **sealed** to each model node's X25519 key. | `ROLE_BROKER` |
| `M/API (P2P)` | `MeshNode(roles={"model"})` — answers with a leased key, signs every answer. | `ROLE_MODEL` |
| `P2P+S` | `MeshNode(roles={"serve","forward"})` — the **decision layer**: takes requests, picks routes, judges answers. | `ROLE_SERVE` |
| `P2P` / `new P2P` | `MeshNode(roles={"forward"})` — carries frames, decides nothing. The standby pool recruited in state ② and the user's way in during state ③. | `ROLE_FORWARD` |
| `user` | `UserClient` — signs requests, re-verifies every answer itself. | — |

- **State ① `new arc`** → `MODE_NORMAL`. Decision layer routes over the confirmed
  link-state graph to distinct providers/models.
- **State ② `当节点失效` (node fails)** → `MODE_DEGRADED`. Crash → central fallback;
  gray failure → suspicion → coordinator quarantine → probation; per-provider
  circuit breaker. The crossed-out "wrong" box is a node that cleared probation.
- **State ③ `模型叛变时` (model mutiny)** → `MODE_LOCKDOWN`. Evidence-latched decision
  layer and coordinator leave the data path; a fresh forwarder carries the
  user's two pinned model nodes; only agreeing answers pass. Only an operator
  signature leaves lockdown.

---

## 3. Deliverable A — Distributed task-delegation schemas

"Distributed extraction task delegation" = the decision layer delegating an
**inference** task across the mesh. The unit of delegation is a signed
`decision` naming one leg per provider; carriers forward it only along the exact
signed path. All objects are canonical JSON, domain-separated, Ed25519-signed
(`secdogie-identity`); `digest(x)` = SHA-256 over canonical JSON.

```
request/v1   (user)     req_id, user, models[1..4], input(≤8000), scopes,
                        quorum[1..3], pins[[provider,model]], issued_at, expires_at(≤+300s)
decision/v1  (decider)  request_hash, decider, mode, epoch, attempt(<3),
                        legs[1..3]{provider, model, path[decider..provider ≤8, no repeat],
                        via: mesh|central}, issued_at
response/v1  (model)    request_hash, decision_hash, provider, model, lease,
                        status: ok|refused, output(≤8000), reason, issued_at
```

Delegation frames on ChannelMux channel `inference/v1` (the transport
authenticates the neighbour; nothing is believed beyond the signed contents):

```
submit  user → entry            {request}
req     hop  → next hop         {request, decision, leg, hop}
resp    hop  → previous hop     {decision, response, leg, hop}
nack    hop  → previous hop     {decision, leg, hop, at, unreachable, reason}
result  decider/fwd → user      {request_hash, items:[[decision,response],…]}
grant   broker → model node     {lease, sealed}      # SealedBox over {lease_id, secret, canary}
```

**Delegation invariants** (enforced, not advisory):

- A carrier forwards `req` only when it sits at `path[hop]` **and** the frame
  arrived from `path[hop-1]` (`resp`/`nack`: from `path[hop+1]`).
- A `mesh` leg never passes through a coordinator; a `central` leg is exactly
  `decider → coordinator → provider`; no provider appears twice.
- The decider self-checks every plan before signing; a bad plan ⇒ it signs
  nothing and floods `decider.self-reported`.
- **This delegates model-inference work only.** There is no frame kind for
  "fetch this URL and return its DOM for me." Adding one would cross §1.3 and is
  out of scope for this layer.

---

## 4. Deliverable B — In-memory structural parser bindings (owned-machine scope)

Structure-first perception already exists and is the project's stated direction
(`ROADMAP.md`, Track D). It is **not** a mesh capability and **not** pointed at
targets the operator does not own. The binding fuses two *structured* senses in
`agent/observation.py` and never silently lets one override the other
(disagreement ⇒ `ObservationConflict`, lower fused confidence, re-observe at the
action gate):

| Sense | Source | Role | Invariant |
| --- | --- | --- | --- |
| `ax` | AX tree (macOS AX / Windows UIA): role / name / automation_id | semantic identity | read-only |
| `dib` | `native/atlas` read-only in-process bitmap rebuild, carried **by reference** (id/hash, never pixels) | verification that AX's claim was actually drawn | read-only |

Process-memory reads (`native/atlas`) are bound to the `SECURITY.md` mask and do
not loosen here: `PROCESS_VM_READ | QUERY[_LIMITED]_INFORMATION` only; **no**
`WriteProcessMemory` / `CreateRemoteThread` / `PROCESS_ALL_ACCESS`; skip
`PAGE_GUARD`/`PAGE_NOACCESS`/execute pages; refuse lsass/csrss/PPL by image name;
query the target token `TOKEN_QUERY` only; refuse SYSTEM/TI/higher integrity;
close the handle before returning. *Perception of a machine you own, not a dump
of the OS security boundary.* Large visual data is always referenced by content
hash — it never enters the Python heap or the event log. There is no `pixel`
path in the fusion layer.

---

## 5. Deliverable C — Three-state fallback routing logic

`mode` is computed independently by each participant:

```
lockdown   if any latch is held
degraded   else if any advisory lists failed/quarantined nodes, or any known node is stale
normal     otherwise
```

Only a valid operator `reset/v1` leaves lockdown.

**State ① → ② transitions** (self-healing, no human in the loop):

- **Liveness** — alive while heartbeats with higher `seq` keep arriving; down
  after `fail_after` (7 s). A request that dies with a node times out; the retry
  avoids it; with no mesh path left the decider routes
  `decider → coordinator → model` (central fallback).
- **Repair** — the coordinator's `advisory/v1` proposes `bypass` links between a
  failed node's live neighbours and `recruit`s a standby forwarder for any model
  node left with no transit link. A link exists only once **both** ends confirm
  it in their own heartbeats.
- **Suspicion** (each decider) — +1 per interior node of a timed-out leg, −1 per
  interior node of a delivered leg; at `suspect_threshold` (2) the node is
  excluded locally for `suspect_cooldown` (60 s) and reported once (`fault/v1:
  timeout`).
- **Quarantine** (coordinator `FaultTracker`) — needs `quarantine_threshold` (2)
  **distinct** reporters within `quarantine_window` (60 s), so one liar cannot
  isolate an honest node. Cooldown doubles per strike (cap 1 h), then probation
  (30 s); one report during probation re-quarantines; a clean probation
  reinstates (the crossed-out "wrong" box). Strikes forgiven after ten clean
  cooldowns.
- **Breaker** (each decider, per model node) — closed → (3 consecutive failures)
  → open (30 s) → half-open → success closes / failure re-opens for twice as
  long, cap 600 s.

**State ② → ③ — the Safety Decision Gate (mutiny by evidence, never by word):**
an `incident/v1` carries signed objects from which any receiver re-derives the
violation against its own trust roots. A verified incident floods; every
participant latches the target; **any latch is lockdown**.

| Kind | Proof | Latches |
| --- | --- | --- |
| `provider.equivocation` | two signed answers, same node/request/model, different output | node |
| `provider.lease-violation` | answer with a forged / borrowed / wrong-model lease | node |
| `provider.secret-exfiltration` | answer containing the canary its lease commits to | node |
| `model.forbidden-capability` | answer to an action-bearing request proposing a never-grantable capability | model + node |
| `model.self-reported` | leaseholder caught its upstream before signing | model |
| `decider.tampering` | a signed decision that downgrades quorum, swaps a model, ignores pins, breaks lockdown shape, or routes through a node quarantined at its own epoch | decider |
| `decider.self-reported` | serving node's guard caught its own policy proposing such a route and refused to sign | decider |

This is the "halt + hard-veto on adversarial instruction injection" the brief's
State ③ asks for: a `forbidden-capability` answer coaxed out by injected web/DOM
content still latches — the mesh **fails closed instead of guessing intent**, and
the evidence carries the user-signed request so the operator can see who asked.

**In lockdown:** serving nodes refuse requests and the coordinator carries no
data; the broker **revokes** the latched node's leases and **rotates** every key
it held (a key that cannot be rotated is never lent again); the user picks a
standby forwarder, `dir?`-fetches a directory, **pins two model nodes itself**
(distinct nodes, distinct models first), and accepts only if both answers verify
and agree. A single-model request cannot be cross-checked, so it is refused.
Latches never time out; an operator `reset/v1` names the targets and the exact
incident ids it reviewed, and a re-arriving incident after its reset does not
latch again.

---

## 6. Invariants this ADR preserves

1. No mesh frame fetches third-party web state on another node's behalf (§1.3, §3).
2. The sandbox fetch stays HTTPS + allowlist + no-credentials + no-redirect + bounded.
3. Perception stays read-only, structure-first, on owned machines; no write/VM-op/thread handles; protected processes refused.
4. Real-world action stays behind capability authorization + HITL.
5. The mesh fails closed: any verified incident ⇒ lockdown ⇒ operator-signed reset only.

Conformance is covered by `inference/tests/test_states.py` (the three drawings
end to end), `test_mutiny.py`, `test_router.py`, `test_topology.py`,
`test_health.py`, and the sandbox-policy tests under `symbiont/`.
