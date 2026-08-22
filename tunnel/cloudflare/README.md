# Cloudflare named tunnel (production reachability)

The custom `tunnel/` in this repo is a from-scratch encrypted-UDP VPN. It has
**not** had an independent cryptographic audit (`SECURITY.md`). For anything
where a real adversary is on the wire, prefer a reviewed path:

- [WireGuard](https://www.wireguard.com/), or
- a **named Cloudflare Tunnel** (`cloudflared`) — outbound-only QUIC to the
  Cloudflare edge, TLS terminated there, optional Cloudflare Access in front.

This directory holds the operator-facing config. The generator scripts live
next to the Atlas native module:

- [`../../native/atlas/scripts/setup-cloudflare-tunnel.sh`](../../native/atlas/scripts/setup-cloudflare-tunnel.sh)
- [`../../native/atlas/scripts/setup-cloudflare-tunnel.ps1`](../../native/atlas/scripts/setup-cloudflare-tunnel.ps1)
- template: [`cf_tunnel_config.json`](cf_tunnel_config.json)

```sh
# Linux / macOS (cloudflared in PATH)
../../native/atlas/scripts/setup-cloudflare-tunnel.sh secdogie-atlas atlas.example.com

# then
cloudflared tunnel --config ~/.cloudflared/config.yml run secdogie-atlas
```

The origin in the template is `http://127.0.0.1:17890` (the `open/` web UI /
agent sidecar). Change the ingress service if you expose a different local
port. Put Access in front of the hostname before you share it. Never commit
the credentials JSON `cloudflared tunnel create` writes.

The custom tunnel stays for air-gapped labs and for learning the handshake;
it is not replaced, only wrapped as a non-default option.
