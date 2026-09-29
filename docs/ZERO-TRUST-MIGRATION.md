# Zero trust by default (stage 2, Wave B): migration notes

Until this change, several constructors and command-line tools treated a
missing allowlist as "trust anyone". That default is gone. A component that
decides whom to hear now refuses to start without being told. The one way to
say "anyone" is explicit and loud:

- **In code, pass `secdogie_identity.ALLOW_ANY`.** It is for tests and local
  development only. `None` raises a `ValueError` that names the component and
  mentions `ALLOW_ANY`.
- **On the command line, pass `--insecure-dev`.** The process prints a warning
  every time it starts this way.

An empty `Allowlist()` is not "unset". It is a strict decision to trust no one.

## Breaking changes

### Constructors

| Component | Before | Now |
| --- | --- | --- |
| `transport.DirectUDPTransport(identity, channel, allowlist=…)` | `None` accepted every signer | required |
| `transport.HubTransport(allowlist=…)` | `None` let anyone register | required |
| `transport.MembershipView(allowlist=…)` | `None` merged anyone's record | required |
| `transport.RendezvousServer(identity, allowlist=…)` | `None` indexed anyone | required |
| `transport.PeerIdentity.from_binding(binding, allowlist=…)` | `None` accepted any binding | required |
| `RoutingTable.add_signed`, `find_node`, `find_peer` (DHT) | `None` accepted any record | required |
| `citadel.Journal(path, allowlist=…)` | `None` merged anyone's events | required |
| `citadel.Supervisor(…, issuers=None)` | no capability gate at all | every mutating action is refused. The gate is always passed to the task runner. `unrestricted=True` turns the check off, for tests and local development only. |
| `fleet.FleetServer(…)` with no signer or allowlist | unauthenticated coordinator | refused; needs `insecure_dev=True` |
| `fleet.node.connect_and_serve(…)` with no identity or allowlist | unauthenticated node | refused; needs `insecure_dev=True` |
| `console.ConsoleController(fleet)` | unsigned commands | refused; needs `operator_allowlist=`, or `allow_unsigned_local=True` for this machine's loopback UI |

### Task runners

The `Supervisor` now always passes `plan_gate=` to its task runner. A runner
that does not accept that argument fails its goal. It no longer runs ungated.
Add `plan_gate=None` (or `**kwargs`) to its signature and hand the gate to the
agent loop.

### Command-line tools

- **`secdogie-citadel`.** Every command needs `--authorized ALLOWLIST`, the
  authors whose events the journal accepts, or `--insecure-dev`.
  - Without `--issuers`, `run` refuses every mutating action and says so.
  - `--insecure-dev` without `--issuers` turns the capability check off, with a
    warning. High-risk steps still ask on the terminal.
- **`secdogie-console` and `secdogie-desktop`.**
  - The fleet needs `--identity` and `--authorized`, or `--insecure-dev`.
  - Without `--operator-authorized`, console commands are unsigned. They are
    accepted only from the loopback UI, and startup logs a warning saying so.
- **Unchanged.** `secdogie-fleet` already worked this way. So do
  `secdogie-relay`, which requires `--authorized`, and
  `secdogie-dialogue connect`, where the `--node` DID is the whole trust set.

### Verify primitives

`verify_payload`, `verify_binding` and `verify_record` keep an optional trust
argument, because they are building blocks. A repository-wide test
(`identity/tests/test_trust_call_sites.py`) fails if any production call site
omits it or passes a literal `None`.

- **Rendezvous client.** It now verifies a server reply against a one-DID
  allowlist naming the pinned server.
- **`secdogie-identity verify-binding`.** Without an allowlist it now states
  that its result checks the signature only.

## Not changed, on purpose

- **The memory projection.** `citadel.consolidate.build_memory(events, trust=None)`
  reads events from a journal whose merge already checked their authors. There,
  `trust` is an extra, retroactive filter for revocation, and the `Supervisor`
  always passes the journal's allowlist.
- **The C tunnel and the WebRTC signaling worker.** They are outside this change;
  their hardening is planned for stage 3.
