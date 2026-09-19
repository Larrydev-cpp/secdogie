# secdogie-console

A local **web control console** for a [secdogie fleet](../fleet) coordinator.
Watch connected nodes and tasks, and submit / stop / pause / resume work from
the browser. A human control panel, so an operator stays in the loop.

- **Loopback only.** The UI binds `127.0.0.1`, the same trust model as
  [secdogie-open](../open). For remote reach, front it with the repo's
  [cloudflared + Cloudflare Access](../tunnel/cloudflare) setup rather than
  opening a port.
- **Operator-DID-gated commands.** With `--operator-authorized`, every mutating
  command must carry a valid operator-DID signature
  ([secdogie-identity](../identity)); reads stay open over loopback. Without it,
  the console is loopback-trusted (single operator on their own machine).

## Run

```sh
pip install -e identity -e fleet -e console
secdogie-console --fleet-port 47810
# nodes dial the coordinator (secdogie-fleet node --connect ...); the browser
# opens the console UI on a 127.0.0.1 port.
```

Secure (DID-authenticated coordinator + signed operator commands):

```sh
secdogie-identity genkey coordinator.key
secdogie-identity genkey operator.key
# put the node DIDs in authorized-nodes.conf, the operator DID in operators.conf
secdogie-console --identity coordinator.key --authorized authorized-nodes.conf \
                 --operator-authorized operators.conf
```

## API

| Method / path | Body | Response |
|---|---|---|
| `GET /api/state` | — | `{requires_signature, nodes[], tasks[], settled, paused}` |
| `POST /api/command` | `{op: submit\|stop\|pause\|resume, ...}` (operator-signed when gated) | `{op, task_id}` / `{op, ok}`, or `403` / `400` |

The command body is the same signed-envelope shape the fleet uses, so a signed
client reuses `secdogie_identity.sign_payload`.
