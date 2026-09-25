# secdogie-node

A running node of the mesh. It puts the tested libraries together in one process:

- **Transport**: one UDP port with DID-signed frames. If `transport_key` is set, frames are encrypted (v2).
- **Membership gossip**: nodes find each other without a central directory.
- **Journal replication**: the signed journal is copied to every node, in size-bounded chunks. It holds goals, run records, capability grants and Socratic reviews.

The node only keeps network state and journal state in sync. **It does not run goals.** You run goals with `secdogie-citadel run`, which applies capability checks, the Socratic review and high-risk confirmation. You start the node yourself; it installs no service and no autostart.

## Configure

```
identity      = node.key          # secdogie-identity genkey node.key
journal       = node.db
listen        = 0.0.0.0:7400
authorized    = authorized.conf   # authorized_did = did:key:... (one line per node)
transport_key = node.tkey         # optional: secdogie-tunnel genkey node.tkey -> encrypted frames
peer          = did:key:z... 203.0.113.7:7400
binding       = peers/b.binding.json   # secdogie-identity bind <key> <transport_pub> > b.binding.json
announce_host = 203.0.113.9       # optional: the address other nodes should use
sync_interval = 15
```

With encryption on, each node writes its own signed binding into the journal. Other nodes pick it up through replication. You only configure bindings by hand for the bootstrap peers.

## Run

```
secdogie-node run node.conf       # foreground; Ctrl-C stops it
secdogie-node status node.conf    # what the node holds, read from its files
```
