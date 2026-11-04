# Obtaining inference-mesh nodes

Chinese: [NODES.zh.md](NODES.zh.md)

The inference mesh ([`inference/`](../inference/README.md)) runs only on machines whose
owners chose to run it. This document describes the three ways it gains nodes, how each one
keeps zero trust intact, and what is deliberately excluded. Terminology follows
[`GLOSSARY.md`](GLOSSARY.md).

| Source | Who runs it | Roles | How it is trusted | How it finds peers |
| --- | --- | --- | --- | --- |
| Operator-controlled machines | the operator, on owned or rented servers | any (serve, forward, model, coordinator, broker) | its DID is on the mesh allowlist, and on the role list for gated roles | static `--peer` entries or a rendezvous record |
| Automatic discovery | — (how listed nodes link up) | — | never widens trust: only DIDs already on the mesh list are dialed | rendezvous lookups plus flooded heartbeats |
| Browser volunteers | a person who opens `symbiont/mesh.html` and clicks **Join** | forward only | an operator admission (`admission/v1`, at most 30 days) for that browser's DID | dials the WebRTC lobbies of the entry nodes its join bundle names |

## 1. Machines the operator controls

Each node is a foreground process, `secdogie-inference run`, which installs nothing. It
prints one JSON line when ready, logs JSON lines to stderr, and exits 0 on SIGTERM / SIGINT,
or when its own DID is revoked by the Master set.

```sh
secdogie-identity genkey node.key                 # Ed25519 identity (0600)
# trust/ holds six allowlists, one `authorized_did = did:key:…` line per DID:
#   mesh.allow users.allow deciders.allow coordinators.allow brokers.allow operators.allow
secdogie-inference run --identity node.key --trust-dir trust/ \
    --role serve --role forward --listen 0.0.0.0:7960 --public-host node1.example.net \
    --bootstrap rendezvous.json [--masters masters.conf --revocations revocations.jsonl]

secdogie-inference run ... --role model --lease-key lease.x25519 --backend mypkg.upstream:call
secdogie-inference run ... --role broker --keys keys.json --assignments assignments.json
secdogie-inference run ... --role coordinator --standby DID
```

- Every trust list is required; a missing list means the node refuses to start.
- `--backend MODULE:FUNCTION` is the upstream API call of a model provider node; no vendor is
  hard-coded.
- `--heartbeat-every` scales every timer (failure detection, leg timeouts, advisories).

## 2. Automatic discovery

Discovery is how listed nodes find and link to each other without hand-written peer
addresses:

1. **Bootstrap.** A rendezvous server is a relay with the rendezvous role, run on any
   allowlisted machine:

   ```sh
   secdogie-relay --identity relay.key --authorized trust/mesh.allow --rendezvous \
       --listen 0.0.0.0:7946 --public-host relay.example.net --record-out rendezvous.json
   ```

   Nodes started with `--bootstrap rendezvous.json` register their local and reflexive
   endpoints with it. Requests are DID-signed and replay-protected, as in
   `transport/secdogie_transport/rendezvous.py`.
2. **Resolve on demand.** An endpoint is looked up when a node first needs to send to a DID:
   - at most 8 lookups run at once, and a failed lookup is retried after 5 s;
   - a send that finds no path triggers a fresh lookup, so a peer that registered late is
     still reached.
3. **Learn the mesh.** After the first link, flooded heartbeats fill each node's link-state
   database with every live mesh DID and its roles.
4. **Keep the degree** (`secdogie_inference/discovery.py`). Each node keeps between
   `--min-degree` (3) and `--max-degree` (6) witnessed links to transit nodes (serving or
   forwarding).
   - Links to the coordinator or the key broker carry no routes, so they do not count.
   - Candidates must be live, not excluded, on the mesh list, and not admitted browsers.
   - Lower load is preferred, with ties spread by a per-node hash.
   - A dial that has not become a witnessed link within 20 s is dropped. Confirmed links are
     never torn down by discovery.
   - The coordinator's repair advice still applies on top.

Discovery never dials outside the mesh list. Being reachable is not the same as being
trusted.

## 3. Browser volunteers

A browser tab can lend its connectivity to the mesh as an admitted, **forward-only** node
([`symbiont/src/mesh/`](../symbiont/src/mesh/)). The flow involves both sides:

1. **Entry nodes.** The operator runs one or more nodes with
   `--signaling wss://…/ws` (the unchanged Cloudflare Worker in
   [`webrtc/signaling/`](../webrtc/signaling/)). Each keeps a WebRTC lobby room,
   `"sdl-" + sha256("lobby|DID|salt")[:40]`.
2. **Identity.** The volunteer opens `mesh.html` (served from a `symbiont` build) and
   clicks **Create identity**. The page makes a non-extractable Ed25519 key that lives in
   that tab only, shows its DID, and the volunteer sends the DID to the operator.
3. **Admission and bundle.** The operator signs a forward-only, expiring admission and wraps
   it in a join bundle:

   ```sh
   secdogie-inference admit --operator-key op.key --subject did:key:… --ttl 86400 > admission.json
   secdogie-inference bundle --trust-dir trust/ --admission admission.json \
       --signaling wss://signal.example.net/ws --entry DID_S --entry DID_P > bundle.json
   ```

   `bundle` refuses an admission that does not verify against the listed operators, and
   entry nodes that are not on the mesh list.
4. **Join.** The volunteer pastes the bundle and clicks **Join**. User activation is
   required, and a banner shows the node's state.

What happens next:

- The browser dials each entry node's lobby and makes the offer, with every ICE candidate in
  the SDP.
- Both sides exchange a signed `rtc-hello/v1` that binds each DID to the DTLS fingerprints
  of this connection, so the signaling gateway cannot sit in the middle. The browser's hello
  carries its admission.
- The entry node verifies the admission and publishes it; it floods, so every full node
  accepts the DID. Each full node records the browser's roles as `forward` only, whatever its
  heartbeats claim (`LinkStateDB.forward_only`).
- The browser signs `heartbeat/v2` heartbeats that witness its neighbours, so its links count
  as witnessed and routes can pass through it.

What the browser node does, and what it never does:

| Does | Never does |
| --- | --- |
| Floods heartbeats, advisories, faults, incidents, resets, lease revocations and admissions, each checked against the role that must have signed it | Decides routes, serves a request, answers as a model, holds a lease or an API key |
| Carries `req` toward the model node and `resp` / `nack` back, only where the signed decision puts it on the path | Carries a non-Lockdown decision while it holds an unreset mutiny incident, or carries for a latched decider or to a latched hop |
| Answers `dir?` and `inc?`, and asks entry nodes for their directory on each new link | Stores anything: no IndexedDB, localStorage or cookies (the `symbiont` red lines) |
| Runs only while the tab is open, after a click | Runs in the background, or reconnects after the tab is closed |

Caps: at most 8 links, frames up to 64 KB, 100 frames/s per neighbour sustained (burst 300),
512 queued frames. Excess is dropped and counted.

### Limits of browser nodes

- **One tab, one DID.** The key is not stored, so a new tab is a new DID and needs a new
  admission. Admissions are short-lived anyway (1 day by default, 30 days at most).
- **The bundle is unsigned.** Nothing in it is believed beyond what it proves:
  - the admission must verify against the bundle's operator list and name this browser;
  - entry nodes must be on the bundle's mesh list, and the DTLS handshake checks them again.
- **Revocation is enforced by the full nodes.** The browser does not evaluate Master
  revocations. A revoked browser DID is refused by every full node (`RevocationFilter` over
  the admission book), which is what removes it from routes.
- **Lockdown belief is conservative.** The browser holds incidents signed by a trusted mesh
  node or user and reads the targets their evidence names. It does not re-run the evidence
  verifiers of `mutiny.py`, so it may refuse more than a full node would, never less.
- **Forwarders see the prompt.** As for every forwarder: transport encryption protects each
  hop, but end-to-end sealing to the model provider node is not implemented yet.

## Ports and NAT

| Component | Transport | Default | Notes |
| --- | --- | --- | --- |
| `secdogie-inference run` | UDP | `0.0.0.0:7960` | `--public-host` is the advertised address; behind NAT, add `--relay-record` relays (`FailoverTransport`) |
| `secdogie-relay` (relay / rendezvous) | UDP | `--listen`, e.g. `0.0.0.0:7946` | `--record-out` writes the record nodes bootstrap from |
| Signaling gateway | WSS | 443 (Cloudflare Worker) | pairs two peers per room; carries SDP only, never mesh frames |
| WebRTC DataChannels | UDP (ICE) | ephemeral ports | host candidates only unless `--ice-server` (nodes) or `ice_servers` (bundle) name STUN/TURN; TURN is needed when both ends are behind symmetric NAT |

## What is excluded

These are not ways to obtain nodes, and nothing in this repository implements them:

- **No covert recruitment.** No scanning for hosts, no exploiting or installing on machines
  without their owner's consent, no botnets.
- **No background persistence.** `run` is a foreground process that installs nothing; the
  browser node ends with its tab.
- **No widening of trust.** Every node is on a trust list or carries an operator admission,
  and an admission grants the forward role only and expires.
- **No traffic obfuscation or detection evasion.** Frames are plain, signed protocol
  objects, and the WebRTC carrier is standard DTLS.

## Tests

| Area | Test |
| --- | --- |
| Discovery to degree over the simulated mesh; the resolver never blocks | `inference/tests/test_discovery.py` |
| A real rendezvous and six `run` processes discover each other and answer `ask`; a killed forwarder is routed around | `inference/tests/test_run.py` |
| Admission rules, flooding, forward-only roles, Master revocation beating an admission | `inference/tests/test_admission.py` |
| `RtcWire`: hello binding, untrusted vs admitted peers, simultaneous dials, a request over DataChannels | `inference/tests/test_rtc.py` |
| Golden vectors: the browser signs heartbeats and hellos byte-identical to Python | `symbiont/tests/mesh.test.ts`, `inference/tests/test_mesh_vectors.py` |
| End to end: Playwright Chromium joins a Python mesh; a request is answered over S → browser → P; closing the tab removes the browser from every route | `inference/tests/test_browser_node.py` (skips without the `symbiont` toolchain and Playwright) |
