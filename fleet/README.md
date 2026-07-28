# secdogie-fleet

Run **one agent per isolated desktop** — a VM, or its own user session — and
coordinate them from the host. Several big tasks then genuinely act **at the same
time**, instead of taking turns on one mouse.

```
  host                                    each Windows VM / session
┌────────────────────────┐               ┌──────────────────────────────┐
│ secdogie-fleet         │  JSON / TCP   │ secdogie-fleet node          │
│   coordinator          │◄──────────────┤   (dials out)                │
│ · node registry        │ assign ─────► │   └─ secdogie-agent drives   │
│ · queue + dispatch     │ ◄──── status  │      THIS desktop, with its  │
│ · concurrency cap      │ ◄──── result  │      own mouse/keyboard/     │
│ · requeue on node loss │               │      foreground window       │
└────────────────────────┘               │   └─ its own API key         │
                                         └──────────────────────────────┘
```

## Why this exists (and how it differs from `open/`)

[`open/`](../open) splits **one** machine's screen by window and drives several
agents against it. But that machine has exactly **one** mouse, one keyboard and
one foreground window, so `agent/`'s input lock has to serialize every click and
keystroke: those agents *think* in parallel and *act* in a queue, fighting each
other for focus. Add windows and it gets worse, not faster.

A fleet gives each task a whole desktop of its own. Nothing is shared, so nothing
has to be serialized — N tasks really do run at once, and one wedged VM doesn't
take the others down with it.

| | `open/` | `fleet/` |
|---|---|---|
| Unit of isolation | a window | a whole desktop (VM / session) |
| Concurrency | thinking only; input is serialized | genuine — separate input queues |
| Focus fighting | yes, agents steal it from each other | none, each owns its desktop |
| Blast radius of a crash | the whole process | one node |
| Cost | free | one guest OS per task |

## Install

**Host (coordinator)** — pure standard library, no display needed:

```sh
pip install -e fleet
```

**Each guest (node)** — needs the agent it drives:

```sh
pip install -e agent        # or the packaged secdogie-agent.exe
pip install -e fleet
```

Set that guest's own API key the usual way (`secdogie-agent --init-config`, an
env var, or a `secdogie.env`) — see [`agent/README.md`](../agent/README.md).

## Run

On the host:

```sh
secdogie-fleet coordinator --port 47810 --auto \
    --task "tidy the downloads folder" \
    --task "check for updates"
```

Inside each VM/session:

```sh
secdogie-fleet node --connect 192.168.56.1:47810 --label win11-vm-1
```

Nodes **dial out**, so guests behind NAT need no inbound firewall rule — only
the host needs a reachable port. Each node reconnects with backoff if the
coordinator goes away.

Useful coordinator flags:

| Flag | Effect |
|---|---|
| `--max-concurrent N` | cap tasks in flight across the whole fleet even if more desktops are free — the real ceiling is usually the shared API quota, not the VMs |
| `--max-attempts N` | how many desktops may try a task before it's marked failed (default 2) |
| `--desktop-ax` | ask nodes to run element-aware (each needs its platform a11y library) |
| `--dry-run` | nodes log what they would do, touching nothing |

## How work is scheduled

- Tasks queue; each idle node takes **one**. More tasks than desktops just wait.
- A node that **drops** (crashed VM, pulled network) hands its task back to the
  queue, and a **different** desktop picks it up — retrying on the box that just
  failed usually just fails again.
- Agent exit codes `0/2/3/5` (done, declined, steps exhausted, stopped) mean the
  task is *finished*, not broken, so it is never retried elsewhere. Other codes
  are infrastructure failures and are retried, up to `--max-attempts`.
- `--auto` matters here: nobody is sitting at a guest to answer a per-step `y/N`.
  High-risk actions (the `open` action) still **fail closed** on a node with no
  terminal rather than running unconfirmed.

## Safety

Everything in [`agent/README.md`](../agent/README.md)'s safety section applies —
times however many desktops you run. Point this only at machines you own. A
coordinator can hand a node any task, so **only run a node on a network you
trust**: the protocol has no authentication, and an assignment's options are
restricted to an allowlist of agent flags (`ALLOWED_OPTIONS` in `node.py`) but
the task text itself is arbitrary. Bind the coordinator to a host-only/private
network, not a public interface.

## Honest limits

- **Windows editions.** Windows 10/11 Home/Pro allow only **one interactive
  session** — connecting over RDP disconnects the console user, so you cannot get
  several sessions on one consumer install. Real multi-session needs Windows
  Server + RDS (with CALs) or Windows 11 Enterprise multi-session (Azure Virtual
  Desktop). **On consumer Windows the supported path is VMs**, one guest OS
  licence each. Third-party session patches are unsupported and not recommended.
- **Resources are the new ceiling.** Budget ~2 vCPU / 4 GB per Windows guest;
  four desktops is already ~16 GB. The practical fleet size comes from host RAM,
  not from anything here.
- **It does not fix rate limits.** Isolation cures input contention, not API
  quota. N nodes = N× the input tokens per minute, and a vision step is ~1.8k
  input tokens (an image is ~55% of a step). Mitigations: give each node its
  **own** API key (nodes resolve their own config, so this is just setup), and
  `--max-concurrent`. The agent now backs off and retries on 429/529 instead of
  dying, so a fleet degrades in throughput rather than collapsing.
- **Screenshots never cross this wire.** Each node captures, calls the model and
  acts locally; only status text returns. Adding nodes costs the host no
  bandwidth — it costs API quota.
- **No authentication or encryption on the wire.** Private network only. If the
  host is remote, carry that hop over [`tunnel/`](../tunnel) — note the tunnel
  itself is Linux-only, so it links *hosts*, not Windows guests.
- **A silently dead guest** (paused VM, blackholed network) is only noticed when
  TCP keepalive eventually gives up, which can take minutes. A crashed or
  disconnected guest is detected immediately.

## Layout

```
secdogie_fleet/
  protocol.py     hello/status/result + assign/stop, JSON codec + validation  [pure, tested]
  coordinator.py  node registry, queue, dispatch, cap, requeue-on-loss        [pure, tested]
  node.py         receives assignments, runs the agent loop on THIS desktop   [runner injected]
  server.py       the coordinator's sockets/threads around coordinator.py     [transport]
  cli.py          secdogie-fleet coordinator | node
tests/            protocol codec, scheduling lifecycle, node dispatch, and an
                  end-to-end run over real sockets (two nodes proven to act
                  concurrently via a barrier that deadlocks if they serialize)
```

## Test

```sh
pip install -e agent && pip install -e fleet && pytest fleet/tests -q
```
