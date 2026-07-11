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
kernel pick `tunN`).

## Hub mode (one node, many clients)

The `server`/`client` pair above is strictly point-to-point. To reach several
machines through one public node — e.g. a controller that drives many agent
boxes — run that node as a **hub** instead: it terminates one tunnel per client
and routes packets between them (and to itself) by inner destination IP.

Clients are unchanged: each is an ordinary `client` whose `endpoint` points at
the hub and whose `peer_public_key` is the hub's. Only the hub gets a new
config shape — no `peer_public_key`, but one `peer` line per client giving that
client's public key and the tunnel IP it will use:

```
# hub.conf
private_key = <hub private key>
address     = 10.66.0.1/24
listen_port = 51820
peer = <client 1 public key> 10.66.0.2
peer = <client 2 public key> 10.66.0.3
```

```sh
sudo ./build/secdogie-tunnel hub hub.conf
```

Each client dials in with its own handshake; the hub identifies which client a
handshake is from by trying each configured `peer` key, and demultiplexes data
packets by the session id every datagram carries — no protocol change. A packet
a client sends to `10.66.0.3` is decrypted at the hub, matched to that client's
slot, and re-encrypted to it, so clients reach each other through the hub.
Because the hub decrypts to route, it can see inter-client traffic — see
[`PROTOCOL.md`](PROTOCOL.md).

## Memory safety

This is C parsing bytes straight off a public UDP socket, so memory safety is
treated as a first-class concern, and two structural choices remove whole
classes of bug up front:

- **No dynamic allocation.** There is no `malloc`/`free` anywhere in the runtime
  — the hub's peer table is a fixed `peers[SDTP_HUB_MAX_PEERS]` array, sessions
  live in caller-owned structs, and every `memcpy` uses a compile-time constant
  length (a key/session-id size), never an attacker-supplied one. So there are
  no leaks, use-after-frees, or double-frees to have.
- **Single-threaded.** The server and hub are one `poll()` loop; there are no
  threads and no locks, so there are no data races or lock ordering to get
  wrong. (Per wake it drains the whole queued UDP batch in one `recvmmsg` — see
  Latency — which is a syscall count change, not a concurrency one.)

What remains is the real risk: a malformed datagram driving an out-of-bounds
read in a parser. That surface is covered by `tests/fuzz_packets.c`, which
blasts random, valid-then-corrupted, and oversized datagrams through every
function that touches network bytes (`sdtp_data_decrypt`,
`sdtp_handshake_respond`, `sdtp_hub_parse_ipv4_dst`, the peer lookups). It runs
as part of `ctest`; for the real proof, build with sanitizers so any overread,
overflow, UB, or leak becomes a hard failure:

```sh
cmake -S . -B build-asan -DSDTP_SANITIZE=ON
cmake --build build-asan
ctest --test-dir build-asan --output-on-failure   # protocol + fuzz, clean under ASan+UBSan+LSan
./build-asan/fuzz_packets 5000000                  # or a longer soak run
```

None of this makes the *cryptography* audited (see [`PROTOCOL.md`](PROTOCOL.md)
and `SECURITY.md`); it's about not crashing on hostile input.

## Latency

The data plane's latency is dominated by three things — the `recvfrom`/`sendto`
syscalls, libsodium's AEAD, and network RTT — none of which the *language*
changes (libsodium is already optimized C; syscalls are the kernel's; RTT is the
wire). The lever that *is* available is the **per-packet syscall count**, which
is what dominates cost under load. So each `poll()` wake drains the whole queued
UDP batch in a single `recvmmsg` (`sdtp_udp_recv_batch`, `src/net.c`) instead of
one `recvfrom` + one poll round-trip per packet.

The effect, measured on loopback by `bench_recv` (built + run as a ctest):

```
$ ./build/bench_recv 50000
received 800000 datagrams (16 per burst, 50000 rounds)
  one recvfrom/pkt :   800000 recv syscalls, ...
  recvmmsg batches :    50000 recv syscalls, ...
  -> 16.0x fewer receive syscalls, ~1.0x wall-time
```

**Honest framing:** the deterministic win is **~16× fewer receive syscalls**
(one per batch instead of one per packet). On loopback the wall-time barely
moves — a loopback `recvfrom` is already cheap — but on a real NIC under load
the saved syscalls and context switches are where tail latency comes from. A
single, idle packet's latency was already near-optimal (`poll` wakes on it
immediately, then one syscall + one AEAD); batching does nothing for that case
and everything for the under-load case. It's non-blocking (`MSG_DONTWAIT`), so a
spurious wake never stalls the loop. `recvmmsg` is Linux; other platforms fall
back to a bounded `recvfrom` loop with identical behavior.

Deliberately **not** done: threads / an event-loop library (the single `poll`
loop is correct and auditable for a handful of peers — adding concurrency would
only add attack surface and races). Known follow-ups if a workload ever needs
them: the symmetric TUN-read drain and `sendmmsg` on the send side, and
`io_uring` for a bigger syscall-amortization step — all in C, no rewrite.

## Layout

```
include/    public headers (protocol constants, module APIs)
src/        implementation
  crypto.c      libsodium wrappers: X25519, BLAKE2b KDF, XChaCha20-Poly1305
  handshake.c   the 3-DH handshake state machine (see PROTOCOL.md)
  data.c        data-channel AEAD framing + replay window
  tun.c         Linux TUN device creation/configuration (ioctl-based)
  net.c         UDP socket helpers + batched receive (recvmmsg drain)
  config.c      config file parsing
  hub.c         hub config loader + multi-client routing event loop
  hub_route.c   pure hub routing helpers (inner-IP parse, peer lookup)
  main.c        CLI (genkey/server/client/hub) + the poll() event loop
tests/      standalone unit tests for crypto/handshake/data/hub (no networking)
PROTOCOL.md wire format + cryptographic design + limitations
```
