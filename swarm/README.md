# SecDogie Symbiotic Runtime

This package is the transport-neutral core for the next SecDogie slice. It does **not** replace the existing Python identity, journal, Gate 2, or WebRTC implementations. Instead, it gives those layers a typed TypeScript/WASM contract for three missing concerns:

1. public-source topology work can continue in isolated workers while the local node is unavailable;
2. queued state mutations can wait for an attention gap instead of interrupting active flow;
3. Gate 2 can surface an action hash as an inline conversation object while preserving the existing Ed25519, short-lived, per-action authorization model.

## Trust boundaries

`PublicFetchPolicy.allowedOrigins` is mandatory in practice: the worker ships with no implicit public-origin trust. Every fetch is HTTPS-only, strips query and fragment material, omits credentials, refuses credential-bearing URLs, and uses a byte/time budget. Redirects are not followed by the worker. A deployment that needs more origins must add them through its own signed configuration layer.

The topology graph is append-only in v1. A delta is a content-addressed set of vertices and edges. Concurrent deltas can therefore merge by set union without pretending to solve general multi-writer deletion semantics. Deletion/tombstones are deliberately a later protocol revision.

The WASM module is a conservative lexical adapter, not a JavaScript parser. A real AST engine can implement the same route-evidence output without changing the swarm wire. This distinction is intentional: the framework provides the execution boundary and determinism guarantees without claiming language-level parsing it does not contain.

Attention signals are structural and coarse. The engine never captures screen pixels and never executes a queued mutation. Sensitive contexts suppress proposals. Flow state defers them. A proposal becomes `ready` only after an attention gap; high-risk proposals still require the Gate 2 path.

Gate 2 keeps the existing repository semantics: the operator key remains off-node, the approval commits to the exact `action_hash`, the subject DID is bound, and the resulting authorization is short-lived. `Ed25519Verifier` is expected to enforce the trusted-operator policy as part of verification, mirroring the existing `TrustPolicy` boundary.

## Integration shape

```text
                ┌─────────────────────────────┐
                │ WebRTC / DID-auth transport │
                └──────────────┬──────────────┘
                               │ typed swarm envelopes
                  ┌────────────▼─────────────┐
                  │ SwarmCoordinator          │
                  │  have/want + delta log    │
                  └───────┬─────────┬────────┘
                          │         │
                    workers       graph
                          │         │
                public source      G-set
                + route adapter    convergence

OS AX/UIA ──► AttentionEngine ──► ProposalQueue ──► Dialogue surface
                                                  │
                                                  ▼
                                           Gate2 Challenge
                                                  │
                                         Ed25519 operator sig
                                                  │
                                                  ▼
                                             ReleaseRecord
```

The package is deliberately headless: transport, OS accessibility adapters, and UI rendering remain dependency-injected edges. This makes each state machine testable without a desktop, credentials, or a network peer.

## Build

```sh
cd swarm
npm install
npm test
```

WASM:

```sh
cd swarm/wasm
wasm-pack build --target web
```
