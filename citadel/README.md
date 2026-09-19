# secdogie-citadel

The **Citadel state substrate**: a signed, append-only event journal and a DAG
goal tree projected from it. This is the durable, tamper-evident, convergent
state layer the supervised Citadel runs on -- persistent across restarts, and
mergeable across a node's own authorized peers without a central writer.

- **Signed & authored.** Every event is Ed25519-signed by its author DID
  ([secdogie-identity](../identity)) and chained to that author's previous event
  (per-author hash chain, like the agent's execution trace). Only the holder of a
  DID's key can write as that DID; altering any past event breaks its chain.
- **Convergent.** `merge()` is idempotent and self-verifying; events read back in
  a deterministic total order `(lamport, ts, author, seq)`, so nodes that have
  seen the same events derive the same state. Single-writer-per-key, so no CRDT
  yet (add an LWW-Map on top only when concurrent multi-writer keys appear).
- **Authorized only.** `merge()` accepts events only from DIDs on the allowlist.
- **No network here.** Storage is stdlib `sqlite3`; replication rides the
  [fleet](../fleet) transport (a later slice).

## Use

```python
from secdogie_identity import Identity, Allowlist
from secdogie_citadel import Journal, build_goal_tree

me = Identity.generate()
j = Journal("citadel.db", identity=me, allowlist=Allowlist({me.did}))
j.append("goal", {"op": "add", "id": "g1", "title": "tidy the desktop"})
j.append("goal", {"op": "add", "id": "g2", "deps": ["g1"]})

tree = build_goal_tree(j.events())
tree.ready()        # ["g1"]  (g2 blocked on g1)
ok, reason = j.verify()
```

## CLI

```sh
secdogie-citadel verify citadel.db   # re-derive chains + signatures
secdogie-citadel goals  citadel.db   # projected goal tree (ready * / order)
secdogie-citadel log    citadel.db   # events in total order
```
