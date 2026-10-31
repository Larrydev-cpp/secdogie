# secdogie-inference

The inference mesh ("new arc"): users' signed requests travel over the P2P mesh to
**model nodes** that hold short key leases from a **key authority**, and the answers
come back signed end to end. A deterministic safety layer judges them at the deciding
node *and* again at the user. When nodes fail, traffic routes around them and falls
back to the central server. When a model or the decision layer itself turns
adversarial, the mesh latches into lockdown. Only an operator's signature gets it out.

Headless and loopback-testable. No UI, no physical-action path, no new crypto:
signing, allowlists and revocation come from `secdogie-identity`, sealing from PyNaCl,
and the wire can ride `secdogie-transport`'s `ChannelMux` (`MuxWire`).

## The three states, as drawn

| Drawing | Here |
| --- | --- |
| central server | `Coordinator`: signed advisories, repair plans, the central fallback path. Not a trust root. |
| API key | `KeyBroker`: lends upstream credentials as short leases, sealed to each model node's X25519 key. |
| M/API (P2P) | `MeshNode(roles={"model"})`: answers with a leased key and signs every answer. |
| P2P+S (four in drawing ①) | `MeshNode(roles={"serve", "forward"})`: the **decision layer**. It takes user requests, picks routes and judges answers. In drawing ② the outer two are the ones that go "(wrong)". |
| new P2P | `MeshNode(roles={"forward"})`: carries frames and decides nothing. The standby pool hangs off the API key; one is recruited for a model node in state 2 and is the user's way in during state 3. |
| user | `UserClient`: signs requests and re-verifies every answer itself. |

### ① normal: the new arc

```mermaid
flowchart LR
  C["central server<br/>Coordinator"] --- K["API key<br/>KeyBroker"]
  K -. sealed lease .-> L
  K -. sealed lease .-> R
  L["M/API<br/>vendor-a"] --- S1["P2P+S"] --- S2["P2P+S"] --- S3["P2P+S"] --- S4["P2P+S"] --- R["M/API<br/>vendor-b"]
  C --- S1
  C --- S4
  U((user)) -->|signed request| S3
  S2 -->|signed answers| U
```

As drawn: four serving nodes, the central server linked to the outer two, the user
attached to the inner two. The user's arrows go in through one and out through the
other; here the answers come back from whichever decider took the request, since
the user re-verifies everything and the return path carries no trust.

Every node floods a signed heartbeat with its roles, its neighbours, its load and, for
a model node, its public leases. A link counts only when **both ends** list each other.
A serving node routes over that graph with `Router`: the cheapest paths, interior nodes
that are transit-only, distinct providers, distinct models first, and node-disjoint
paths for a quorum above one. It signs a **decision** naming each leg's path. The
model node checks the request and the decision itself, answers, and signs a
**response** that embeds its lease and binds to the request and the decision. Nothing
in between can alter it.

### ② a node fails: connect to the central server

```mermaid
flowchart LR
  C["central server"] -.->|"central attach"| L
  C -.->|"central attach"| R
  S2 ==>|"bypass"| L
  N1["new P2P"] --- L
  N1 --- K["API key"]
  L["M/API"] --- S1["(wrong) P2P"]:::bad --- S2["S+P2P"] --- S3["S+P2P"] --- S4["P2P (wrong)"]:::bad --- R["M/API"]
  S3 ==>|"bypass"| R
  N2["new P2P"] --- R
  N2 --- K
  classDef bad stroke-dasharray: 5 5
```

* **Crash.** A node that stops sending new heartbeats ages out of everyone's view.
  The first request that dies with it times out. The retry avoids it, and if the mesh
  has no other path, the decider routes `decider → coordinator → model node`, which is
  the central fallback. A user refused with `no route` waits a little and asks again
  while the mesh repairs itself.
* **Repair: connect to the central server.** The coordinator's next advisory proposes
  **bypass** links between the failed node's live neighbours, and the coordinator links
  itself to those same neighbours (the drawing's title, and its lines from the central
  server to both M/API). It **recruits** a standby forwarder (the "new P2P") for any
  model node left with no transit link, linked to that model node and to the API key
  as drawn. Nodes open the links that name them, and a link only exists once both ends
  confirm it in their own heartbeats.
* **Witnessed links (the heartbeat DAG).** Every heartbeat names its predecessor and,
  for each neighbour, the newest heartbeat that neighbour handed it directly. A link
  counts only while both ends witness each other recently, so a node that stops
  hearing a neighbour takes that link out of everyone's routes through its own signed
  heartbeat. Two heartbeats that fork one node's chain prove its key runs twice: the
  node is isolated at once, without lockdown, until an operator resets it.
* **Wrong node (gray failure).** Some nodes heartbeat fine but drop traffic. Each
  decider's `Suspicion` charges the interior of legs that time out and credits legs
  that deliver. A node past the threshold is avoided locally and reported to the
  coordinator; the signed report is flooded, since a decider need not be linked to it. The coordinator's `FaultTracker` quarantines a node only on reports from
  `quarantine_threshold` **distinct** reporters, so one liar cannot isolate an honest
  node. The quarantine cooldown doubles on each repeat. After it comes **probation**:
  one more report sends the node back, and a clean probation reinstates it (the
  crossed-out "wrong" in the drawing).
* Ordinary failures (timeouts, refusals, invalid or divergent answers) feed a per-node
  `CircuitBreaker` that recovers on its own: closed → open → half-open.

### ③ the decision model mutinies: lockdown

```mermaid
flowchart LR
  CS["c/s"] --- K["API key"]
  K --- L["M/API"]
  K --- R["M/API"]
  L --- N["new P2P<br/>(standby forwarder)"]
  R --- N
  N --- U((user))
```

Mutiny is decided by **evidence**, never by anyone's word. An incident carries signed
objects from which any receiver re-derives the violation against its own trust roots
(see `mutiny.py`):

| Kind | Proof | Latches |
| --- | --- | --- |
| `provider.equivocation` | two signed answers, same node / request / model, different output | the node |
| `provider.lease-violation` | an answer with a forged lease, someone else's, or one for another model | the node |
| `provider.secret-exfiltration` | an answer containing the canary its own lease commits to | the node |
| `model.forbidden-capability` | an answer to an action-bearing request proposing a never-grantable capability | the model and the node |
| `model.self-reported` | a leaseholder caught its upstream model before signing | the model |
| `decider.tampering` | a signed decision that downgrades quorum, swaps a model, ignores pins, breaks lockdown shape, or routes through a node quarantined at its own epoch | the decider |
| `decider.self-reported` | a serving node's guard caught its decision policy proposing such a route and refused to sign it | the decider |
| `node.heartbeat-fork` | two signed heartbeats of one node that fork its chain: its key runs twice | the node, **isolated** without lockdown (drawing ②, not ③) |

A verified incident floods the mesh. Every participant latches the target, and **any
mutiny latch is lockdown** (a heartbeat fork only isolates its node), because the decision layer may share one decision model, so one
mutiny makes all of it suspect. In lockdown:

* serving nodes refuse requests, and the coordinator carries no data;
* the broker revokes the latched node's leases and **rotates** every key it held. If
  the key source cannot rotate, the exposed key is never lent again;
* the user picks a standby forwarder, fetches a directory, and **pins** two model
  nodes itself (distinct nodes, distinct models first). The forwarder links straight to
  them, signs a decision that is exactly the pins, and returns every answer unjudged.
  The user accepts only if both answers verify and agree. A single-model request
  cannot be cross-checked, so it is refused.

Latches never time out. An operator signs a reset (`make_reset`) naming the targets
and the incident ids it reviewed. A reset cannot clear an incident it does not name,
and an incident that arrives again after its reset does not latch again.

## The mode as a state machine

Each participant runs a `ModeMachine` (`state.py`): `normal` (drawing ①), `degraded`
(②) and `lockdown` (③). A move must have a cause the table allows; only an
operator's reset leaves lockdown, and any other exit raises `IllegalTransition`. Moves
are kept as `Transition` records and can be subscribed to:

```python
node.machine.subscribe(lambda t: print(t.old, "->", t.new, "because", t.cause))
node.health_snapshot()   # per node: alive, suspect, quarantine, breaker, latched, isolated
```

## Using it

```python
from secdogie_inference import (TrustRoots, MeshNode, Coordinator, KeyBroker, UserClient,
                                MemoryKeySource, MuxWire, assignments_from_bindings)

trust = TrustRoots(mesh=..., users=..., deciders=..., coordinators=..., brokers=..., operators=...)
wire = MuxWire(mux, resolve=lambda did: transport.set_peer_endpoint(did, *endpoints[did]))
node = MeshNode(identity, wire, trust, roles={"model"}, backend=call_upstream, transport_key=x25519)
wire.attach(node)                  # inbound frames on the inference/v1 channel
node.connect(peer_did)             # links are opened explicitly, and only to trusted DIDs
while running:
    node.tick()                    # heartbeats, timeouts, advisories, lease renewal
```

`backend(model, input, secret) -> str` is where an upstream API client goes. Every
trust set is required. `ALLOW_ANY` is for tests only, as everywhere in secdogie.

## Tests

```sh
pip install -e identity -e transport -e inference pytest
cd inference && python -m pytest tests -q
```

* `test_states.py`: the three drawings end to end on a deterministic simulated mesh
  (`tests/sim.py`, drawing ①'s topology as drawn): routing, a lying decider, multipath;
  a failed decider whose neighbours connect to the central server, the central fallback,
  the bypass and the new P2P, the black hole quarantined by two deciders and then
  reinstated; decision-model mutiny with lockdown and reset, a convicted decider, a
  leaking model node and key rotation, an upstream model proposing `run_elevated`,
  equivocation caught by multipath, disagreement intercepted.
* `test_state.py`: the mode machine. Every (from, to, cause) move is taken exactly when
  the table allows it, only a reset leaves lockdown, the three drawings as recorded
  transitions, and a latch cleared behind the ledger's back failing loudly.
* `test_session.py`: session edges, admission per node and per user (`busy`), expiry,
  late answers for closed sessions, bounded retention, the user's `take`/`forget`,
  revoked-id forgetting, wiped lease secrets, the ledger's holder cap failing closed,
  and a **soak**: twenty simulated minutes of crashes, a black hole, lease renewals,
  lockdown and reset and a stream of requests, after which every table is under its cap.
* `test_merkle.py`: RFC 6962 roots against the Certificate Transparency vectors, a
  full heartbeat inside one frame, a restored backup's fork isolating its node without
  lockdown (and the reset), one incident id whoever reports a fork, a link whose end
  stops hearing the other leaving every route, and a node that missed three incidents
  catching up from its neighbours' ledger roots.
* `test_udp.py`: one request across real UDP (`DirectUDPTransport` + `ChannelMux`).
* Unit tests for every module: protocol, leases, screens, topology, health and
  breaker, mutiny evidence and ledger, router.

## Bounded by construction

Every request a serving node routes is a session with an explicit lifecycle in a
bounded table (`session.py`): at most 64 at once and 8 per user (`busy: …` beyond
that), each with an expiry, and closing one releases everything it held. A user's
asks are bounded the same way; collect outcomes with `take(handle)`. Every other
table -- answers, equivocation memory, leases, revoked ids, fault records, suspicion,
the mutiny ledger -- has a bound, listed in PROTOCOL.md. None of them grows with what
arrives; the soak test checks that.

## Honest limits

* **Forwarders see the prompt.** Transport sealing protects each hop on the wire, but
  sealing the input end to end to the model node is not built yet.
* **One frame, one datagram.** Inputs and outputs are capped at 8000 characters. There
  is no chunking.
* **Online propagation, then anti-entropy.** Incidents, resets and lease revocations
  flood to the nodes that are up. A node that was down notices from its neighbours'
  `ledger_root` and fetches what it lacks (`dir?`, then `inc?` by id); a user catches
  up through directories. The ledger is in memory. Persisting it, the way
  `RevocationStore` persists revocations, is the obvious next step.
* **Forks need a shared past.** A restored backup or a copied process continues the
  same chain and is caught. Two copies of a key started from scratch each begin a new
  chain (`GENESIS`, their own seq); receivers see gaps, not a provable fork.
* **Agreement is strict.** `default_agree` compares JSON structurally and text after
  normalising whitespace and case. For free-form answers, plug in a comparator.
* **The canary catches dumps, not care.** A model node that strips the canary before
  leaking the key is not caught by it. Rotating on every node-level latch is the
  backstop.
* **Prompt-induced mutiny is still mutiny.** A `forbidden-capability` answer may have
  been coaxed out by its prompt. The evidence carries the user-signed request, so the
  operator can see who asked and revoke that user. The mesh fails closed instead of
  guessing intent.
* **Secrets in a garbage-collected heap.** Held lease secrets are `bytearray`s, zeroed
  on expiry and revocation, but the backend receives a `str`, and opening the grant
  makes one too; CPython cannot zero those. The guarantee is that nothing references a
  secret longer than it must, not that it is erased from the process.
* **Operator-sized meshes.** Link-state flooding costs O(nodes × links) per heartbeat
  interval. That is fine for tens of nodes, and it is not designed for internet scale.
