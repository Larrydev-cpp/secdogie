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
  node's transport (`secdogie-node`, the mesh).

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

## Socratic gates and staged memory

`socratic.py` questions an instruction; `action_gate.py` questions each planned
action -- including the Gate 1 intent contract (which active goal it serves, how
to back out or an explicit "irreversible", whether its preconditions still hold)
-- and `authz.py` is Gate 2, the operator's signature on anything irreversible.

Memory is staged ([MEMORY.zh.md](MEMORY.zh.md)):

| Stage | Module | Where it lives | Trust |
| --- | --- | --- | --- |
| S1 episodic | `episodes.py` | a view of the journal's run / step events | signed; only verified, finished runs are learned from |
| S2 candidate | `lessons.py` | local SQLite, never replicated | untrusted quarantine; never read by a gate or a prompt |
| S3 consolidated | `consolidate.py` | signed `memory` events, replicated | cautions on re-derived evidence; facts only on an operator-signed confirmation |

`MemoryView.known_failures` feeds `GateContext.known_failures`. Memory can only
make the gates stricter; Gate 2 never reads it.

## CLI

```sh
secdogie-citadel verify citadel.db --authorized nodes.allow   # re-derive chains + signatures
secdogie-citadel goals  citadel.db --authorized nodes.allow   # projected goal tree (ready * / order)
secdogie-citadel log    citadel.db --authorized nodes.allow   # events in total order
secdogie-citadel run    citadel.db --identity node.key --authorized nodes.allow --issuers operators.allow
```

Zero trust: every command needs `--authorized` (whose events the journal
accepts), and `run` refuses every mutating action unless `--issuers` names who
may grant this node capabilities (`secdogie-identity grant`, then `add-grant`).
`--insecure-dev` lifts both for a throwaway local test, with a warning. In code,
`Journal` requires an allowlist and `Supervisor` without issuers is deny-all
(`unrestricted=True` is the explicit, test-only opt-out); see
[../docs/ZERO-TRUST-MIGRATION.md](../docs/ZERO-TRUST-MIGRATION.md).

`add-goal`, `run`, `add-grant` and `scopes` take `--masters masters.conf
--revocations revocations.jsonl`. Events from a revoked author stop merging, and
grants from a revoked issuer stop counting. `run` does not start if this node's
own DID is revoked, and halts (exit 0) if the revocation arrives mid-run.
