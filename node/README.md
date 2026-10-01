# secdogie-node

The resident node: one foreground process that puts the pieces together.

- **Transport.** A DID-authenticated UDP transport that hears only the operator
  Apps you list. Frames are optionally encrypted with an X25519 transport key.
  Given `--relay-record` (a relay's self-signed record, as `secdogie-relay`
  prints it), the node also reaches the App through that relay whenever it has
  not heard the App directly of late. Given `--rendezvous-record` (as
  `secdogie-relay --rendezvous` prints it), the node registers there and keeps
  renewing, so an App finds it by DID (`secdogie-dialogue connect
  --rendezvous-record`) without being told its address.
- **Operator dialogue.** For the connected App, a dialogue session over which
  the node sends Gate 2 challenges, Socratic probes, the structural view and
  memory offers, and receives control requests.
- **Supervised loop.** A signed journal and a Supervisor that run each goal
  through the real agent loop, with Gate 1 (intent and known-failure memory),
  Gate 2 (operator signatures) and staged memory (S1–S3).
- **The mesh (stage 3).** The same transport also hears the nodes on `--mesh`.
  - **Gossip.** The nodes gossip their self-signed membership records, so a
    node started with one `--bootstrap-record` learns the rest and can reach
    them.
  - **Replication.** They replicate their journals in datagram-sized batches.
    What one node learns reaches the others: a caution earned by one node's
    failing runs is refused at the others' Gate 1 on their first try. Each
    node still runs only its own goals; a peer's goals, stops and interrupted
    runs arrive in the journal but are never run, obeyed or recovered here.

- **Revocations that last (T6).** Given `--masters`, a Master-signed
  revocation record is applied to every trust set the node holds, however it
  arrives:
  - the fast gossip frame;
  - the journal, replicated, so a node that was offline catches up;
  - the operator's store (`--revocations`, `revoke-apply`).

  The node then writes the record into its journal so it lasts. A revoked peer
  stops being heard, and a revoked App loses its session. A node revoked while
  it was away is cut off by everyone, so when it knocks it is sent the records
  that revoke it; it checks them against its own masters and halts. A record
  that does not verify changes nothing, on any path.
- **Device class (T7).** `--device-class headless` is for a node without a
  screen. It takes no goals, the goals it holds are not run, and it never
  loads the agent. Its membership record says `headless`, as `secdogie-relay`'s
  does; only a `display` node (the default) acts on a screen.

It assembles what the other packages provide; it adds no protocol of its own.

## Run

```sh
pip install -e ../identity -e ../transport -e ../citadel -e ../dialogue -e ../agent -e .
secdogie-node run --identity node.key \
    --apps apps.allow --operators operators.allow --authorized nodes.allow \
    --mesh mesh.allow [--bootstrap-record other-node.json] \
    --issuers issuers.allow --journal node.db --listen 0.0.0.0:7950
```

It prints one JSON line when it is ready: its DID, the address it listens on,
and its self-signed membership record. Save that line as another node's
`--bootstrap-record`. It logs to stderr, and exits 0 on SIGTERM or Ctrl-C. It never installs
itself or keeps running in the background.

The operator connects with the App:

```sh
secdogie-dialogue connect --identity app.key --node did:key:z6Mk... --node-addr HOST:7950 \
    --operator-keystore op.keystore
```

`secdogie-node status --journal node.db --authorized nodes.allow` prints the
goals and the memory recorded in a journal.

## Zero trust

| Flag | Whom it names | Required |
| --- | --- | --- |
| `--apps` | operator App session keys that may open a dialogue and confirm memory | yes |
| `--operators` | keys whose Gate 2 signatures authorize destructive steps | yes |
| `--authorized` | journal authors (this node included) | yes |
| `--mesh` | the other nodes it gossips and replicates with; each also on `--authorized` (alone: name only this node) | yes |
| `--issuers` | who may grant this node capabilities (`secdogie-identity grant`) | no: without it every mutating action is refused |

- **No capability check needs saying out loud.** `--insecure-dev` without
  `--issuers` turns the capability check off, for a throwaway local test, with
  a warning.
- **High-risk steps always go to the operator.** That holds whatever the flags.
- **Revocation.** `--masters` and `--revocations` make every list
  revocation-aware. The node does not start, or stops, once its own DID is
  revoked.
- **One App at a time.** While the connected App is alive, another App is
  refused. While no App is reachable, the node holds no operator hooks, so a
  step that needs the operator is refused at once: no answer is a no.

## Tests

```sh
pip install -e '../dialogue[tui]' pytest
pytest tests -q
```

- **End to end** (`tests/test_e2e.py`): real UDP on 127.0.0.1, the production
  runner and the real agent loop, with a scripted model and a fake desktop that
  has an accessibility tree. It covers, in order:
  - a destructive step signed through Gate 2;
  - `ask_user` answered by the operator;
  - a model note held in quarantine until the App confirms it;
  - the structural view in the App's inspector;
  - an action that failed three times being refused on the fourth run.
- **Three processes** (`tests/test_multiprocess.py`): `secdogie-relay`, the
  node (the real CLI, with the scripted model patched in by
  `tests/fake_desk_node.py`) and `secdogie-dialogue connect --headless`. The
  App is given a dead address for the node, so every frame goes through the
  relay; the same scenario and checks as the in-process test.
- **Relay fallback** (`tests/test_relay_fallback.py`): the dialogue survives a
  blocked direct path.
- **Process tests** (`tests/test_cli.py`):
  - the node refuses to start without each trust set;
  - it announces itself and stops cleanly on SIGTERM;
  - a revoked node does not start;
  - the App and the node run as two separate processes.
