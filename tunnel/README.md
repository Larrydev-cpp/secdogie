# secdogie-tunnel

A minimal point-to-point encrypted VPN tunnel, written from scratch in C on
top of libsodium. See [`PROTOCOL.md`](PROTOCOL.md) for the wire format and
the cryptographic design (a mutually-authenticated, forward-secret 1-RTT
handshake, followed by an XChaCha20-Poly1305 encrypted data channel over a
Linux TUN device). Read the "Known limitations" section there before using
this for anything beyond personal / educational purposes — it has not been
independently audited.

## Build

Requires a C compiler, CMake, and libsodium (dev headers + pkg-config).

```sh
# Debian/Ubuntu
sudo apt-get install -y build-essential cmake libsodium-dev pkg-config

cmake -S . -B build
cmake --build build -j
./build/secdogie-tunnel        # prints usage
./build/test_protocol          # unit tests for the crypto/handshake/replay logic
```

Creating a TUN device requires `CAP_NET_ADMIN` — run as root, or grant the
capability to the binary (`sudo setcap cap_net_admin+ep build/secdogie-tunnel`).

## Usage

Generate a keypair on each of the two machines that will form the tunnel:

```sh
./build/secdogie-tunnel genkey server.key   # prints the matching public_key to stderr
./build/secdogie-tunnel genkey client.key
```

Exchange the two `public_key` values out of band (they are not secret).
Then write a config file on each side. **Server** (`server.conf`, the side
with a stable, reachable address):

```
private_key = <server private key>
peer_public_key = <client public key>
address = 10.66.0.1/24
listen_port = 51820
```

**Client** (`client.conf`):

```
private_key = <client private key>
peer_public_key = <server public key>
address = 10.66.0.2/24
endpoint = <server ip or hostname>:51820
```

Run:

```sh
sudo ./build/secdogie-tunnel server server.conf
sudo ./build/secdogie-tunnel client client.conf
```

Once the log shows `handshake completed` on both sides, traffic sent to the
peer's tunnel address (e.g. `ping 10.66.0.1` from the client machine) is
carried encrypted over UDP. Verified end to end (real client/server
processes, two network namespaces joined by a veth pair, `tcpdump` on the
link showing only ciphertext) during development — see the project's
commit history / CI for the test harness.

Optional config keys: `mtu` (default 1400), `ifname` (default: let the
kernel pick `tunN`), `dscp` and `busy_poll` (see "Latency tuning" below).

## Latency tuning

The tunnel is built to add as little latency as the software layer allows:
each packet is forwarded the instant it is read (no coalescing / batching
delay), the event loop drains every queued packet per wakeup instead of
paying a `poll()` round-trip per packet, and the UDP socket is set up for
low delay. A few knobs let you push further for your path:

- **`dscp = <0..63>`** — DSCP class stamped on the *outer* UDP packets so a
  QoS-aware network can prioritise the tunnel. Interactive traffic usually
  wants Expedited Forwarding, `dscp = 46`. If the key is omitted the socket
  falls back to the legacy `IPTOS_LOWDELAY` bit. Set this on both peers.
- **`busy_poll = <microseconds>`** — enables `SO_BUSY_POLL` on the socket so
  the kernel busy-polls the NIC for up to that long instead of waiting for an
  interrupt, shaving wakeup latency at the cost of CPU. Try `busy_poll = 50`.
  Also requires a nonzero `net.core.busy_poll` sysctl to affect `poll()`.
- **MTU / fragmentation** — keep `mtu` low enough that the outer packet
  (inner MTU + ~48 bytes of UDP/AEAD overhead) stays under the path MTU;
  fragmentation adds reassembly delay and drop risk. 1400 is safe on a
  standard 1500-byte path; lower it for PPPoE, nested tunnels, etc.
- **Scheduling** — for the lowest tail latency under contention, pin the
  process to an isolated core and/or run it at real-time priority
  (`chrt -f 10 ./secdogie-tunnel …`); the receive buffers are already sized
  to ride out a scheduling gap without dropping.

None of these change the wire protocol, so a tuned peer still interoperates
with an untuned one.

## Layout

```
include/    public headers (protocol constants, module APIs)
src/        implementation
  crypto.c      libsodium wrappers: X25519, BLAKE2b KDF, XChaCha20-Poly1305
  handshake.c   the 3-DH handshake state machine (see PROTOCOL.md)
  data.c        data-channel AEAD framing + replay window
  tun.c         Linux TUN device creation/configuration (ioctl-based)
  net.c         UDP socket helpers
  config.c      config file parsing
  main.c        CLI (genkey/server/client) + the poll() event loop
tests/      standalone unit tests for crypto/handshake/data (no networking)
PROTOCOL.md wire format + cryptographic design + limitations
```
