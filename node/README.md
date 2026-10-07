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
  refused (over the browser link with a signed `busy`).
- **The operator comes and goes; the node does not.** A step that needs the
  operator while no App is reachable (before the first one, or across a closed
  tab or a refresh) waits for one up to the challenge's or question's own
  expiry, and expiry is still a no. When an App says HELLO it is told what the
  node is doing (`status: idle` / `status: running` / `status: waiting for
  operator`, naming the goal) and shown every challenge and question still
  open, unchanged.

## The browser link

With `--webrtc-signal` the node also keeps a room on the signaling gateway
(`webrtc/signaling/`), so the operator page (`symbiont/`) reaches it over a
WebRTC data channel without any address typed in. Install the extra first:
`pip install -e '.[webrtc]'` (aiortc + websockets).

```sh
secdogie-node run ... --webrtc-signal wss://<gateway>/ws --webrtc-origin https://<ui-origin>
```

- **Nothing on the link is trusted until W1.** The gateway only relays offer /
  answer / ICE. Before one frame crosses the channel, the node and the page
  each sign the DTLS fingerprints they see
  (`secdogie/webrtc-dtls-binding/v1`); a gateway in the middle fails that
  check on both sides. Then the link carries the same DID-signed frames as
  UDP, from the bound DID only.
- **Data only.** Any SDP with audio or video is refused before it is answered;
  the node never adds a track.
- **The room** is derived from the node's key (secret, stable across
  restarts); `--webrtc-room-epoch N` moves to a new one, and every browser then
  pairs again. The room is never printed.
- **No snapshots** are sent to an App on the link.

### Pairing a browser (once)

```sh
secdogie-node pair --identity node.key --apps apps.allow --operators operators.allow \
    --webrtc-signal wss://<gateway>/ws --ui https://<ui>/
```

1. It writes a one-time link to this terminal only -- never to stdout or a
   log: the link is a key. It works for `--ttl` seconds (default 600), once,
   and five bad attempts burn it.
2. Open the link in the browser. Both the terminal and the page show the same
   12-digit check code.
3. The terminal asks whether the page shows those digits, and -- if the page
   offers an operator key -- whether this browser may also approve steps that
   cannot be undone (Gate 2). The default for both is **no**.
4. Tap **连接 / Connect** on the page. Only with your "y" here *and* the tap
   are the keys appended to `--apps` (and `--operators`).
5. A running node notices the changed files within seconds, records the
   change in its journal, and the page attaches from then on whenever it is
   opened.

`pair` runs beside the resident node (it meets the page in a room of its own),
needs a terminal, and exits when the pairing is done, refused or expired. To
remove a browser, delete its lines from both files: the running node drops it
within seconds. A browser that is told it is no longer enrolled forgets its
pairing.

### Running it all the time

The node never installs itself. If you want it running in the background, you
write the service unit -- for example:

```ini
# ~/.config/systemd/user/secdogie-node.service  (systemctl --user enable --now secdogie-node)
[Unit]
Description=secdogie node
[Service]
ExecStart=%h/.local/bin/secdogie-node run --identity %h/.secdogie/node.key --apps %h/.secdogie/apps.allow \
    --operators %h/.secdogie/operators.allow --authorized %h/.secdogie/nodes.allow \
    --issuers %h/.secdogie/issuers.allow --journal %h/.secdogie/node.db --webrtc-signal wss://<gateway>/ws
Restart=on-failure
[Install]
WantedBy=default.target
```

```xml
<!-- ~/Library/LaunchAgents/dev.secdogie.node.plist  (launchctl load -w ...) -->
<plist version="1.0"><dict>
  <key>Label</key><string>dev.secdogie.node</string>
  <key>ProgramArguments</key><array>
    <string>/usr/local/bin/secdogie-node</string><string>run</string>
    <string>--identity</string><string>/Users/me/.secdogie/node.key</string>
    <!-- ...the same flags as above... -->
  </array>
  <key>KeepAlive</key><true/>
</dict></plist>
```

On Windows, a Task Scheduler task "At log on" running `secdogie-node.exe run ...`
does the same. Pairing stays a foreground step: run `secdogie-node pair` in a
terminal while the service keeps running.

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
- **Browser link** (`tests/test_webrtc_node.py`, needs the `[webrtc]` extra
  and a non-loopback IPv4 address): a page stand-in pairs (owner "y" + tap),
  the node hears it after re-reading its allowlist, the page attaches over W1,
  says HELLO, and approves a Gate 2 challenge that was raised while no page
  was attached. `tests/test_pairing.py` covers both confirmations, the "no",
  burned and expired offers, and the terminal-only link.
- **Process tests** (`tests/test_cli.py`):
  - the node refuses to start without each trust set;
  - it announces itself and stops cleanly on SIGTERM;
  - a revoked node does not start;
  - the App and the node run as two separate processes.
