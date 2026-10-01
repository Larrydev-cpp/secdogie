# secdogie-identity

Cryptographic **DID identities** (`@Larryx`) for secdogie: a self-certifying
`did:key` derived from an Ed25519 public key, used to **sign and verify**
commands and node handshakes among your own or explicitly-authorized nodes.

- **No registry, CA, or blockchain.** A `did:key:z...` string *is* the public
  key (Ed25519 multicodec, base58btc). Anyone can resolve it and check a
  signature offline.
- **One crypto dependency, PyNaCl** — the Python binding to libsodium, the same
  C library the `tunnel/` links. Nothing here is hand-rolled.
- **Authorization is separate from authentication.** A valid signature proves
  *who* signed; an [allowlist](secdogie_identity/allowlist.py) of authorized
  DIDs decides *whether they may act*.

## CLI

```sh
pip install -e identity
secdogie-identity genkey node.key      # write an Ed25519 identity, mode 0600
secdogie-identity did node.key         # print its DID document (JSON)
secdogie-identity verify node.key authorized.conf   # is this DID authorized?
```

`node.key` is a `key = value` file (mode 0600), same shape as a tunnel key:

```
# did:key: did:key:z6Mk...
# public_key = <base64>   (share this)
signing_seed = <base64>
```

`authorized.conf` lists the DIDs allowed to act, same shape as the tunnel peer
list:

```
authorized_did = did:key:z6Mk...   node-a
authorized_did = did:key:z6Mk...   node-b
```

## Library

```python
from secdogie_identity import Identity, Allowlist, sign_payload, verify_payload

me = Identity.generate()
msg = sign_payload(me, {"kind": "hello", "node_id": "n1"})   # adds signer + sig
ok, signer = verify_payload(msg, Allowlist({me.did}))         # (True, did)
```

The signature covers a canonical (sorted-key, tight) JSON encoding of the
payload — the same encoding `agent/secdogie_agent/trace.py` uses — so `signer`
and `sig` layer onto any JSON contract that ignores unknown keys without
changing it.

## Revocation (R1.2)

In an unattended mesh, authorization has to be withdrawable, and the withdrawal
has to be unforgeable. A **revocation record** is a statement, signed by the
mesh's *master* keys, that one or more DIDs are no longer authorized. It is the
decentralized equivalent of striking a DID off the allowlist.

The masters are a `key = value` file, same shape as the allowlist, with an
optional threshold:

```
master_did = did:key:z6Mk...   master-a (kept offline)
master_did = did:key:z6Mk...   master-b
master_did = did:key:z6Mk...   master-c
threshold  = 2
```

`threshold = k` means `k` of the `n` masters must sign a record for it to count.
A single stolen master key then cannot revoke on its own. With one master the
threshold defaults to 1.

Records are co-signed offline and verified anywhere:

```
secdogie-identity revoke-propose did:key:z6MkVICTIM --reason "key leaked" --out rev.json
secdogie-identity revoke-cosign master-a.key rev.json     # each master signs, in turn
secdogie-identity revoke-cosign master-b.key rev.json
secdogie-identity revoke-verify rev.json masters.conf     # exit 0 once the threshold is met
secdogie-identity revoke-apply rev.json --masters masters.conf --store revocations.jsonl
```

`revoke-apply` checks the signatures again and appends the record to a
revocation store, a JSON-lines file. Every command line that takes an allowlist
also takes `--masters masters.conf --revocations revocations.jsonl`
(`secdogie-node`, `secdogie-citadel`, `secdogie-relay`). Those processes re-read the store every few seconds, so a
record appended there takes effect without a restart:

- a revoked DID is refused wherever the allowlist is checked;
- a node drops the session of an App that was already connected;
- a process whose own DID is revoked stops its work and exits 0.

The store is only a transport for records: a line whose signatures do not meet
the threshold changes nothing. In code, `load_trust_policy(allow_path,
masters_path=..., revocations_path=...)` builds the same thing the command lines
use.

In code, a `TrustPolicy` is an allowlist narrowed by the revocations it has
accepted. It is duck-typed exactly like `Allowlist` (`contains` / `dids`), so it
drops into any component that already gates a DID through `.contains`:

```python
from secdogie_identity import Allowlist, MasterSet, RevocationStore, TrustPolicy

policy = TrustPolicy(Allowlist.load("authorized.conf"),
                     masters=MasterSet.load("masters.conf"),
                     store=RevocationStore("revocations.jsonl"))
policy.contains(did)        # on the allowlist AND not revoked
policy.apply(record)        # verify + merge one record; returns the newly-revoked DIDs
policy.on_change(callback)  # notified of newly-revoked DIDs (for cache eviction / self-halt)
```

**Permanent.** Revocation only ever adds DIDs to the revoked set; it never
restores one. Records can arrive in any order, more than once, over any path,
and the result is the same union. A revoked node rejoins only by minting a fresh
DID and being re-authorized — there is no un-revoke.

**The cost of k-of-n.** An emergency revocation needs `k` masters available to
co-sign at once. Choose the threshold for that trade-off: too high and you
cannot revoke in a hurry, too low and one compromised key is enough. Changing
the master set itself is an offline, out-of-band operation.
