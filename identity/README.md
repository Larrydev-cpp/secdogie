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
and `sig` layer onto any JSON contract that ignores unknown keys (e.g. the fleet
wire protocol) without changing it.
