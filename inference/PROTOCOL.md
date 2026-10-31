# secdogie inference protocol, v2

v2 changes against v1: heartbeats are Merkle-DAG vertices (`heartbeat/v2`); a chain
fork is evidence that isolates a node without lockdown (`node.heartbeat-fork`); fault
reports are flooded instead of sent to a neighbouring coordinator; directories list
held incident ids and `inc?` fetches them; leases are capped at `MAX_LEASE_TTL`. Every
other object keeps its v1 type. v1 and v2 nodes do not interoperate (a v1 node rejects
v2 heartbeats), so a mesh upgrades all at once.

All objects are canonical JSON (sorted keys, tight separators, UTF-8), signed by
`secdogie-identity` (`signer`, `sig` = detached Ed25519 over the payload without those
two keys). Every type is domain-separated, so no signature verifies as another type.
`digest(x)` is SHA-256 over the canonical JSON of the whole signed object.

## Signed objects

| Type `secdogie/inference/…` | Signed by | Fields |
| --- | --- | --- |
| `request/v1` | a user | `req_id` (32 hex), `user`, `models` (1–4, sorted), `input` (≤ 8000), `scopes` (sorted, grantable), `quorum` (1–3), `pins` (`[provider, model]`, ≤ 3, at least `quorum`), `issued_at`, `expires_at` (≤ 300 s later) |
| `decision/v1` | a decider; in lockdown, any mesh node | `request_hash`, `decider`, `mode`, `epoch`, `attempt` (< 3), `legs` (1–3: `provider`, `model`, `path` [decider … provider, ≤ 8 nodes, no repeats], `via` `mesh` \| `central`), `issued_at` |
| `response/v1` | the model node | `request_hash`, `decision_hash`, `provider`, `model`, `lease` (public lease), `status` `ok` \| `refused`, `output` (≤ 8000, empty when refused), `reason`, `issued_at` |
| `heartbeat/v2` | each mesh node | `node`, `roles`, `seq` (strictly increasing), `prev` (digest of this node's previous heartbeat, or `GENESIS` = 64 zeros after a start), `neighbors` (≤ 32 `[did, seq, head]`, sorted by DID: the newest heartbeat that neighbour handed this node directly, by seq and the first 32 hex of its digest; `[did, 0, ""]` if none yet), `load` (0–1), `leases` (≤ 4, own), `ledger_root` (Merkle root over the held `target\|incident_id` pairs), `issued_at` |
| `advisory/v1` | a coordinator | `coordinator`, `epoch` (increasing), `failed`, `quarantined`, `bypass` and `recruit` (DID pairs), `issued_at` |
| `fault/v1` | a mesh node | `reporter`, `accused` (a mesh node, not the reporter), `reason` `timeout` \| `invalid` \| `divergent`, `request_hash`, `issued_at` (± 120 s). Flooded; only coordinators act on it |
| `lease/v1` | a broker | `broker`, `subject`, `model`, `key_version`, `valid_from`, `expires_at` (at most `MAX_LEASE_TTL` = 3600 s after `valid_from`), `canary_hash`, `lease_id` (first 32 hex of the digest of the rest) |
| `lease-revocation/v1` | a broker | `broker`, `lease_ids` (≤ 64), `reason`, `issued_at` |
| `incident/v1` | a mesh node or user | `reporter`, `kind`, `evidence`, `incident_id` = digest of `{kind, evidence}`, `issued_at` |
| `reset/v1` | an operator | `operator`, `targets` (`node:` / `model:` / `decider:` + id), `incident_ids`, `issued_at` |

A decision is structurally valid only if each path runs from the decider to its
provider, a `central` leg is exactly `decider → coordinator → provider`, a `mesh` leg
never passes through a coordinator, and no provider appears twice. **Violations**
are well-formed decisions that work against the request. A decider signs these
knowingly, so they are evidence: fewer legs than `max(quorum, 2 in lockdown)`, a model
the user did not list, legs that are not exactly the pins, or a lockdown decision that
is not all direct `mesh` legs.

**Grant.** A grant (`broker → model node`) carries the public lease plus `sealed`:
`SealedBox(X25519 of the node's verified DID binding)` over canonical
`{lease_id, secret, canary}`. The node accepts it only if the lease id matches and
`sha256(canary) == canary_hash`, so a sealed body cannot be moved onto another lease.
The canary is `sdgc_` followed by 32 hex.

## Frames

A frame is a JSON object `{"k": kind, …}` on ChannelMux channel `inference/v1`. The
transport authenticates the neighbour. Nothing in a frame is believed beyond what its
signed contents prove.

| `k` | From → to | Carries |
| --- | --- | --- |
| `submit` | user → entry | `request` |
| `req` | hop → next hop | `request`, `decision`, `leg`, `hop` (the receiver's index on the path) |
| `resp` | hop → previous hop | `decision`, `response`, `leg`, `hop` |
| `nack` | hop → previous hop | `decision`, `leg`, `hop`, `at`, `unreachable`, `reason` |
| `result` | decider or forwarder → user | `request_hash`, `items` (`[decision, response]` pairs) |
| `refused` | entry → user | `request_hash`, `reason` (`intercepted: …`, `lockdown: …`, `no route: …`, `no quorum: …`, `busy: …`, `expired: …`, …). The user waits `retry_backoff` before asking again after `no route` / `no quorum` from its last untried entry |
| `flood` | any → neighbours | `obj`: heartbeat, advisory, fault report, incident, reset, lease revocation |
| `grant` | broker → model node | `lease`, `sealed` |
| `dir?` / `dir` | any ↔ node | `leases`, newest `incidents` (2), `resets` (8), `incident_ids` (≤ 64 held). A user's `dir?` also attaches it. |
| `inc?` | any → node | `ids` (≤ 16): answered with one `flood` frame per incident the node holds. The asker sends it for the ids in a `dir` it does not know |

A carrier forwards a `req` only when it sits at `path[hop]` and the frame came from
`path[hop-1]` (for `resp` and `nack`, from `path[hop+1]`). It refuses, with a `nack`,
to carry a non-lockdown decision during lockdown, a latched decider's decision, or a
leg whose next hop is latched or unreachable.

## Flows

**Normal and degraded.** The steps run in this order:

1. `submit` arrives at a serving node.
2. The node verifies the request.
3. It builds a view: confirmed links among live nodes, excluding latched, quarantined
   and suspect nodes, and keeping only model nodes with a current lease and a
   non-open breaker.
4. The policy plans legs, and the node self-checks them (shape, violations, no
   excluded hop, every provider eligible). A failed self-check means the node signs
   nothing and publishes `decider.self-reported`.
5. It signs the decision and sends each leg.
6. The model node re-checks the decision against the request. Violations become
   `decider.tampering`.
7. The model node answers through its output guard: lease material, or a forbidden
   proposal for an action-bearing request, gives `refused` plus `model.self-reported`.
8. It signs the response. A repeated request gets the same output.
9. The `resp` frames walk back to the decider.
10. The safety layer adjudicates. Evidence means `refused` with `intercepted: …` first,
    and the incident second. Missing answers mean a retry (up to 3 attempts, avoiding
    what failed, with the central fallback). Disagreement means `intercepted` plus
    `divergent` faults. Acceptance means a `result` to the user.
11. The user verifies everything again.

**Lockdown.** The steps run in this order:

1. The user picks a standby forwarder that is not a decider.
2. If it lacks leases, it sends `dir?`.
3. It pins two model nodes: distinct nodes, distinct models first, deterministic.
4. It floods its newest incidents to the forwarder, then sends `submit`.
5. The forwarder (forward role only, no serve role) checks that each pin is eligible,
   links straight to each pinned node, and signs a lockdown decision that is exactly
   the pins.
6. It returns every item in a `result`. It publishes any evidence it found, but makes
   no judgment.
7. The user needs both answers to verify and agree.

## State machines

**Mode** (`state.ModeMachine`, one per participant). `derive` is the rule:
`lockdown` if any mutiny latch is held, else `degraded` if any advisory lists failed or
quarantined nodes, any known node is stale, or a node is isolated by proof, else
`normal`. `step` moves the machine and checks every move against this table; a move
needs one of the causes listed for it:

| From → to | Causes |
| --- | --- |
| (none) → any | `start` |
| normal → degraded | `liveness`, `advisory`, `isolation` |
| degraded → normal | `liveness`, `advisory`, `reset` |
| normal → lockdown, degraded → lockdown | `latch` |
| lockdown → normal, lockdown → degraded | `reset` **only** |

Causes are what the participant absorbed since the last step (an advisory, an
incident, a reset), plus `liveness`, which time passing can always cause. A move with
no allowed cause raises `IllegalTransition`: latches are cleared only by a verified
operator reset, so leaving lockdown any other way is a bug and fails loudly. Each
move is a `Transition {at, old, new, cause, inputs}` kept in a bounded history (256)
and handed to subscribers; `on_mode(old, new)` still fires.

`Participant.health_snapshot(now)` reads the per-node machines below for every node a
participant knows: `NodeHealth {alive, suspect, quarantine, breaker, latched,
isolated}`.

**Liveness.** A node is alive while new heartbeats (higher `seq`) keep arriving, timed
by the receiver's clock. It is down after `fail_after` (default 7 s).

**The heartbeat DAG** (`topology.LinkStateDB`, every participant). Each node's
heartbeats form a hash chain through `prev`; each heartbeat also names, per neighbour,
the newest heartbeat that neighbour handed it *directly*. Together they are a Merkle
DAG of link state. Each participant keeps the last 8 heartbeats of every node and checks:

* **Chain.** A `prev` that is not the heartbeat held is a gap (lost datagrams), counted
  and not a fault. A `GENESIS` `prev` is a (re)start.
* **Fork.** Two verified heartbeats of one node with the same `seq`, or the same
  non-`GENESIS` `prev`, and different digests: the node's key runs twice (a restored
  backup, a cloned key). The pair, in digest order, is `node.heartbeat-fork` evidence,
  published by whoever sees it; every reporter's incident has the same id.
* **Witnessed link.** `a — b` is in the graph only if both list each other **and** each
  one's witness of the other is at most `max_witness_lag` (3) heartbeats behind the
  newest of the other's held, with a digest head that matches when that heartbeat is
  held. A node that stops hearing a neighbour says so in its own signed heartbeat, and
  the link leaves every participant's routes, with no timeout and on nobody's word.
  A node that hears fine but drops `req` frames is still `Suspicion`'s job.
* **Ledger root.** A neighbour whose `ledger_root` differs from this participant's for
  2 heartbeats in a row is sent `dir?`; ids it lists that this participant does not
  know are fetched with `inc?` (each id at most every 30 s). A node that missed a flood
  catches up from its neighbours.

**Suspicion** (each decider): +1 for each interior node of a timed-out leg, −1 for
each interior node of a leg that delivered. At `suspect_threshold` (2) the node is
excluded for `suspect_cooldown` (60 s) and reported once (`fault: timeout`). Reports
are flooded, because in drawing ① the deciders that see a failure need not be linked
to the coordinator.

**Repairs** (coordinator, each advisory): for every failed, quarantined or latched node,

* `bypass`: links between the live nodes it leaves behind (never model–model);
* the central attach: the coordinator links itself to those same nodes, model nodes
  and deciders alike, and to any model node with no live transit link. This is the
  title of drawing ②, "connect to the central server", and it is what makes a
  `decider → coordinator → model node` leg possible. It is skipped in lockdown;
* `recruit`: for each model node with no live transit link, a standby forwarder (the
  "new P2P") paired with that model node and with a live key broker it is not yet
  linked to.

**Quarantine** (coordinator), for one accused node:

1. Healthy: it takes `quarantine_threshold` (2) distinct reporters within
   `quarantine_window` (60 s) to quarantine it.
2. Quarantined for `quarantine_cooldown × 2^(strikes−1)` (60 s, capped at 1 h).
3. Probation for `probation` (30 s). A single report during probation quarantines it
   again.
4. A clean probation makes it healthy. Strikes are forgiven after ten cooldowns clean.

**Breaker** (each decider, for each model node):

1. Closed until `breaker_threshold` (3) consecutive failures, which open it.
2. Open for `breaker_cooldown` (30 s), then half-open.
3. Half-open: a success closes it. A failure opens it again for twice as long, up to
   600 s.

**Latch** (every participant): a verified incident latches each of its targets until
a reset by a trusted operator names that target **and** every incident id holding it.
Cleared `(target, incident)` pairs are remembered, so the same incident never latches
that target again. A latch held only by `ISOLATE_ONLY` kinds (`node.heartbeat-fork`)
**isolates** its node: off every path, mode `degraded`, cause `isolation`. Any other
latch is lockdown.

**Broker on a latch:**

* `node:` loses its leases, and each model it held is rotated. Older-version leases of
  those models are revoked and fresh leases are re-issued.
* `model:` loses all its leases. It is rotated only if the kind is
  `model.self-reported`.
* `decider:` triggers nothing.
* A key that cannot be rotated is never lent again.

## Session lifecycle and memory bounds

Every request a serving node takes on is a session (`session.py`) in a bounded table,
keyed by the request digest and owned by the user who submitted it:

```
open → deciding → dispatched → settling → closed
          ↑                        │
          └──── retry (attempt+1) ─┘        (any state → closed)
```

* **Admission.** At most `max_sessions` (64) live sessions, and `max_sessions_per_user`
  (8) for one user. A request that does not fit is refused `busy: …` and nothing is
  kept for it, so the same request may be submitted again later.
* **Edges.** Any other move raises `IllegalSessionEdge`; `closed` is terminal. A late
  `resp` or `nack` for a closed session finds no live session and is dropped.
* **Expiry.** A session expires at the earlier of the request's `expires_at` and
  `leg_timeout × MAX_ATTEMPTS + 5 s` after it opened; the node closes it and refuses
  `expired: …`.
* **Close** is the one way out. It drops the request, decisions, legs and answers the
  session held and keeps only the final frame, for repeats: at most `retain_results`
  (256) frames and `retain_bytes` (4 MiB).
* Outcomes: `accepted`, `intercepted`, `refused`, `expired`, `aborted` (lockdown
  arrived), `relayed` (the lockdown forwarder, which judges nothing).

A user's asks follow the same discipline (`open → submitted ⇄ awaiting-dir → closed`):
at most `max_open` (256) open asks, beyond which `ask` returns an immediately refused
`busy: …` outcome; settled outcomes are kept until `take(handle)`, `forget(handle)`,
`retain_for` (300 s) or `retain` (1024) newer ones.

Everything else a participant holds is bounded too:

| What | Bound |
| --- | --- |
| a model node's remembered answers (`_served`) | 4096 and `answer_bytes` (8 MiB) |
| the safety layer's equivocation memory | 4096 answers and 16 MiB |
| flood dedup (`_seen`) | 8192 digests |
| public leases | pruned on expiry, every tick, by every role |
| revoked lease ids | forgotten once the lease is dead: its `expires_at` + `MAX_SKEW`, or for an unseen lease the revocation's `issued_at` + `MAX_LEASE_TTL` + `MAX_SKEW` |
| the coordinator's fault records | accused must be a mesh node; records with no report in the window, no quarantine or probation, and no strike are dropped |
| local suspicion | zero scores and ended cooldowns are dropped |
| the mutiny ledger | `MAX_HOLDERS` (16) incidents per latched target; an incident whose every target is already held that many times is not kept (if it arrives again after a reset, it latches again); cleared incidents become id-only tombstones; newest `MAX_RESETS` (64) signed resets |
| held lease secrets | `bytearray`s zeroed on expiry and revocation |

## Limits (v1)

`MAX_INPUT` = `MAX_OUTPUT` = 8000 characters (one frame, one datagram), `MAX_QUORUM`
3, `LOCKDOWN_QUORUM` 2, `MAX_HOPS` 8, `MAX_ATTEMPTS` 3, `MAX_TTL` 300 s, `MAX_SKEW`
120 s, `MAX_NEIGHBORS` 32 (each with a witness), `MAX_LEASES` 4 per heartbeat (a full
heartbeat stays under 12 KB), `MAX_LISTED` 64 per advisory
list, 64 lease ids per revocation, and `MAX_LEASE_TTL` 3600 s.
