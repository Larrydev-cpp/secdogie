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
