# secdogie-node

The resident node: one foreground process that puts the pieces together.

- **Transport.** A DID-authenticated UDP transport that hears only the operator
  Apps you list. Frames are optionally encrypted with an X25519 transport key.
- **Operator dialogue.** For the connected App, a dialogue session over which
  the node sends Gate 2 challenges, Socratic probes, the structural view and
  memory offers, and receives control requests.
- **Supervised loop.** A signed journal and a Supervisor that run each goal
  through the real agent loop, with Gate 1 (intent and known-failure memory),
  Gate 2 (operator signatures) and staged memory (S1–S3).

It assembles what the other packages provide; it adds no protocol of its own.

## Run

```sh
pip install -e ../identity -e ../transport -e ../citadel -e ../dialogue -e ../agent -e .
secdogie-node run --identity node.key \
    --apps apps.allow --operators operators.allow --authorized nodes.allow \
    --issuers issuers.allow --journal node.db --listen 0.0.0.0:7950
```

It prints one JSON line when it is ready: its DID and the address it listens
on. It logs to stderr, and exits 0 on SIGTERM or Ctrl-C. It never installs
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
- **Process tests** (`tests/test_cli.py`):
  - the node refuses to start without each trust set;
  - it announces itself and stops cleanly on SIGTERM;
  - a revoked node does not start;
  - the App and the node run as two separate processes.
