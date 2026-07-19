# Security Policy

secdogie is a personal, from-scratch toolkit. It is not a hardened,
multi-tenant product, and this policy is written to match what it actually is —
so you know both how to report a real issue and what trust model the code
assumes.

If you believe you've found a security issue, **report it privately first**
(see [Reporting](#reporting-a-vulnerability) below). Please don't open a public
issue or PR that discloses an unpatched vulnerability, exploit path, or a
working proof of concept.

## Trust Model

secdogie is built for **one operator driving machines they own or are
explicitly authorized to control.** Every component assumes it runs inside a
trusted host/operator boundary:

- **The agent moves your real mouse, keyboard, phone, and files.** Anyone who
  can run it, edit its config, or set its environment is a trusted operator.
- **The vision model is not a trusted principal.** Content on the screen it
  looks at is untrusted input, and a malicious page/notification can try to
  steer the model (prompt injection). The safeguards are the per-step
  confirmation (on by default), `--dry-run`, keeping `--auto` off when
  unattended, and pyautogui's corner fail-safe — *not* a claim that the model
  can't be manipulated.
- **API keys are secrets you supply.** They're read from `--api-key`, the
  provider env var, or a config file that `--init-config` creates `chmod 600`,
  and they are never written to the run log. Don't commit them.
- **The tunnel's static keys are secrets.** `genkey` writes private-key files
  `chmod 600`; keep them that way and never commit them.

If multiple, mutually-distrusting people can reach the same running agent or
the same host, that is outside the model — isolate by OS user / host instead.

## The Tunnel Is Not Audited

`tunnel/` is a from-scratch encrypted-UDP implementation (X25519 + a Noise-style
handshake, XChaCha20-Poly1305 AEAD, a per-session replay window). It was built
to learn and to serve this toolkit, and it has **not** had an independent
cryptographic audit. For anything where a real adversary is on the wire, prefer
a reviewed implementation such as WireGuard. Crypto/protocol bug reports here
are very welcome (see in-scope below).

## Reporting a Vulnerability

Report privately through a
[GitHub Security Advisory](https://github.com/larrydev-cpp/secdogie/security/advisories/new)
on this repository. To make a report easy to act on, include:

- what you found and why it's security-relevant;
- the affected component (`agent`, `android`, `ios`, `open`, `scene3d`,
  `tunnel`) and the commit SHA;
- reproduction steps or a proof of concept against the current `main`;
- the actual impact — which boundary above is crossed;
- any fix or mitigation you can suggest.

There is no bug-bounty program; this is a personal project. Careful,
reproducible reports still get a fix as fast as I can manage.

## In Scope

Concrete boundary-crossing bugs, especially:

- **Tunnel memory safety** — out-of-bounds read/write, overflow, or UB in the C
  code reachable from a network peer.
- **Tunnel crypto/protocol flaws** — nonce reuse, a replay-window bypass, a
  handshake authentication or peer-identity bypass, key/keystream recovery, or
  the hub routing a decrypted packet to the wrong session.
- **Secret leakage caused by secdogie's own code** — e.g. an API key or private
  key written to a log, printed, or created world-readable by our code.
- **A path that makes the agent act without the confirmation it promises** —
  e.g. a non-benign action executing without `--auto` and without a `y/N`
  prompt.

## Out of Scope

These follow from the trust model above and are not vulnerabilities by
themselves:

- The agent doing something harmful because you told it to, or because
  on-screen content prompt-injected the model while you ran with `--auto`. That
  is the documented risk of an unattended run — keep confirmations on.
- Running secdogie against a machine, phone, or account you don't control.
- You committing or exposing your own API key / private key.
- A hostile peer flooding the tunnel with UDP; it's a best-effort personal
  tunnel, not a DoS-hardened service.
- Model output quality, hallucinated actions, or cost.
- Anyone who can already edit the config, the environment, or the host — that's
  a trusted operator here.

If you're unsure whether something is in scope, report it privately anyway; a
careful report is always welcome.
