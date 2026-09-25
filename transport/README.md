# secdogie-transport

A **P2P-ready** peer / session / endpoint model. Upper layers (fleet, citadel
sync) depend on an **authenticated peer session** -- a DID bound to a transport
key (via [secdogie-identity](../identity) binding), reachable at migratable
endpoints -- instead of a hub socket.

- **PeerIdentity** is built from a verified DID<->transport binding, so "who is
  this peer" is a cryptographic fact, not an IP.
- **Session** ties a stable `session_id` to a peer; endpoint migration (a NAT
  rebind) changes the active endpoint but never the identity.
- **Transport** is the seam: register an authenticated session, route by DID.
  Today's `HubTransport` is an in-memory hub-and-spoke router (the current
  topology, kept as fallback / rendezvous / relay); a `DirectUDPTransport`
  (Phase 2.10) implements the same interface for true peer-to-peer.

This layer is transport-mechanism-free (no sockets): it is the identity/session/
routing model, backed later by the fleet TCP or the C tunnel.

## P2P.2 -- direct upgrade with relay fallback

- **Session paths.** Every `Session` starts on the relay (`PATH_RELAY`).
  `DirectUpgrader` migrates it to `PATH_DIRECT` only after a verified PROBE ->
  PROBE-ACK round trip (make-before-break: probes use `route_to`, which never
  changes the live route, and the relay stays registered). Each path change bumps
  `Session.epoch`. `fall_back()` puts the session back on the relay endpoint it
  left, after a probe times out or the direct path goes silent (`sweep`).
- **Signed timestamps on every frame.** v1 and v2 direct frames carry `ts` (ms)
  and `ctr` inside the DID-signed envelope. A frame more than `max_skew`
  (default 30 s) away from the receiver's clock is dropped, and duplicates inside
  that window hit the per-peer `ReplayWindow`, so hole-punch traffic cannot be
  replayed. PROBE nonces are random, single-use, and tied to the probed DID.

## Mesh node (2B)

`MeshNode` assembles the pieces into one running peer: one UDP socket (direct
frames to `DirectUDPTransport`, rendezvous frames via its `on_other` hook), a
`Session` + `DirectUpgrader` per peer, membership gossip on the built-in `member`
channel, and upper-layer channels via `add_protocol` (journal replication:
`secdogie_citadel.replication.attach(node, journal)`). Messages are multiplexed
and fragmented by `mux.py` so every frame stays under ~1400 bytes.

- **No server needed.** Any node can `serve_rendezvous=True`; others
  `use_rendezvous(did, host, port)` and `lookup(peer)`. Peers can also be seeded
  with `add_peer(did, endpoints)`.
- **Liveness from round trips only.** A direct path stays up only while
  keepalive PROBE-ACKs come back; one-way traffic does not keep it alive.
- **Ping back.** A verified direct frame from a peer makes its source address an
  upgrade candidate, so the other side dials back without a rendezvous.
- **Concurrency.** Socket and relay callbacks only enqueue; `process()` /
  `tick()` (or `start()`) do all protocol work under the node's own lock.
