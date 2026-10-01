# One window (stage 4): migration notes

Everything the operator does now happens in **one native window**:

- Double-click the exe, or run `secdogie` from a pip install.
- The first time, it asks only for the model's API key.
- After that, one conversation covers the rest:
  - send goals;
  - answer the agent's questions;
  - approve or deny high-risk steps with your passphrase (Gate 2);
  - confirm memory;
  - look at the structural view (视界);
  - drive nodes on other machines.

See [`app/README.md`](../app/README.md). The other ways in are gone.

## What was removed, and what replaces it

| Removed | What it did | Now |
| --- | --- | --- |
| The card menu of a double-clicked `secdogie-agent` exe, and `--menu` | Picked a mode (task, preview, `--auto`, …) | The double-click opens the secdogie window. The CLI flags (`--gui`, `--dry-run`, `--auto`, `--desktop-ax`, …) are unchanged. |
| `secdogie-dialogue connect` without `--headless` (the Textual screen, the `[tui]` extra) | The operator's screen for one node | The window: pair the node from the switcher (添加远程节点). `connect --headless SCRIPT` stays, for scripts and tests. The `[net]` extra replaces `[tui]`. |
| `desktop/` (`secdogie-desktop`) | A tkinter chat window over the fleet | The window. |
| `console/` (`secdogie-console`) | A local web console over the fleet, with no Gate 2 | The window. There is no web UI. |
| `fleet/` (`secdogie-fleet coordinator` / `node`) | A host coordinator sending tasks over TCP to agents on VMs; high-risk confirmations popped up on the VM itself | Each VM runs `secdogie-node`, and the window pairs with each one. |

## Moving a fleet to nodes

Each VM (or user session) runs the ordinary resident node, with its own model
API key on that machine.

1. **Get the two DIDs from the window.** Open the switcher, choose
   "添加远程节点…", and note the **App DID** and the **operator DID**. If the
   passphrase is not set yet, the card sets it first.
2. **Start the node on the VM.** Put the App DID in `apps.allow` and the
   operator DID in `operators.allow`:

   ```sh
   secdogie-node run --identity vm.key \
       --apps apps.allow --operators operators.allow \
       --authorized nodes.allow --mesh nodes.allow \
       --issuers issuers.allow --journal vm.db --listen 0.0.0.0:7950
   ```

3. **Grant the VM's node what it may do.** It needs the desktop scopes, from an
   issuer on its `--issuers` list:

   ```sh
   secdogie-identity grant issuer.key did:key:<vm> --ttl 2592000 \
       --scope observe.read --scope physical.click --scope physical.type --scope physical.key \
       --scope physical.scroll --scope physical.drag --scope system.open > grant.json
   secdogie-citadel add-grant vm.db grant.json --identity vm.key --issuers issuers.allow
   ```

   Without a grant, every mutating action is refused. A grant expires: one day
   by default, 30 days above. Issue a new one before it lapses. The window's own
   node needs none of this, because it is granted afresh at every start.
4. **Pair the VM in the window.** Paste the node's ready line, plus its address
   (`10.0.0.5:7950`), or a rendezvous record if it is behind NAT.
5. **Optionally, form a mesh.** Put every VM on the others' `--mesh` and
   `--authorized`. Their journals then replicate: a caution one VM earned by
   failing reaches the others' Gate 1, and revocations reach every node.

## What is different from the fleet

- **No central queue.** You choose the node for each goal in the switcher.
  Several nodes run their goals at the same time, each in its own
  conversation. The switcher tells you when one of them is waiting for you.
- **High-risk steps are approved from the window, signed.** A step on a VM is
  approved with the operator key (Gate 2), by you, in the window. It is no
  longer a popup on a VM nobody is watching, and a node that does not trust
  your operator DID refuses the step. There is still no switch that turns the
  confirmation off.
- **No automatic requeue on node loss.**
  - A goal interrupted by a crash is resumed by that node when it restarts.
  - A node that is gone stays gone until it comes back.
  - Its journal, and anything it replicated to other nodes, is kept.

## Unchanged

- **The node and the mesh.** `secdogie-node`, `secdogie-relay` and the
  rendezvous, with membership gossip, journal replication and revocations,
  work as in stage 3.
- **The agent CLI.** `secdogie-agent` with arguments, including its `--gui`
  operator console, behaves as before.
- **`webrtc/`** stays in the tree. It is no longer extended, and there is no
  web operator surface.
