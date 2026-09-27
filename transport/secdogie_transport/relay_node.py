"""A standalone, unattended relay node: ``secdogie-relay`` (2C.1).

`RelayService` makes relaying a role any allowlisted node can take on; this is
the process that lets a headless machine -- a VPS, a NAS -- take it on by
itself, with nobody at the keyboard::

    secdogie-relay --identity relay.key --authorized mesh.allow \\
                   --listen 0.0.0.0:7946 --public-host relay.example.net

It binds one UDP socket, serves the relay role on it, prints its self-signed
membership record (``roles=["relay"]``) as one JSON line so other nodes can be
bootstrapped with it, logs a ``stats`` line to stderr every so often, and runs
until SIGTERM / SIGINT, when it stops serving and exits 0.

What it deliberately does not do:

  * No human in the loop. It never reads stdin and has no confirmation hook:
    whether a frame is forwarded is decided by signatures and the allowlist
    alone, on the receive thread. It imports nothing from the agent, Citadel
    or fleet packages -- the embodied-action layer, where human confirmation
    lives, is not part of this process at all.
  * No unauthenticated mode. ``--identity`` and ``--authorized`` are required;
    there is no "serve anyone" switch.
  * No self-installation. It runs in the foreground; the operator decides how
    to supervise it (systemd unit, container), in the open.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
import threading
import time

from secdogie_identity import Allowlist, Identity

from .endpoint import Endpoint
from .membership import ROLE_RELAY, sign_record
from .relay import DEFAULT_LEASE, MAX_LEASE, RelayService
from .udp import DirectUDPTransport, UDPChannel

DEFAULT_LISTEN = "0.0.0.0:7946"
DEFAULT_STATS_EVERY = 60.0
_WILDCARD_HOSTS = {"", "0.0.0.0"}


def _listen(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit() or not 0 <= int(port) < 65536:
        raise argparse.ArgumentTypeError(f"expected HOST:PORT (IPv4, port 0-65535), got {value!r}")
    return host, int(port)


def _positive(limit: float | None = None):
    def parse(value: str) -> float:
        try:
            number = float(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
        if not number > 0 or (limit is not None and number > limit):
            bound = f"in (0, {limit:g}]" if limit is not None else "> 0"
            raise argparse.ArgumentTypeError(f"must be {bound}, got {value!r}")
        return number
    return parse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="secdogie-relay",
        description="Run an unattended relay node for the secdogie mesh.",
    )
    p.add_argument("--identity", required=True, metavar="KEYFILE",
                   help="this relay's Ed25519 key file (secdogie-identity keygen)")
    p.add_argument("--authorized", required=True, metavar="ALLOWLIST",
                   help="allowlist of the DIDs this relay serves (authorized_did = did:key:... lines)")
    p.add_argument("--listen", type=_listen, default=DEFAULT_LISTEN, metavar="HOST:PORT",
                   help=f"UDP address to bind (default {DEFAULT_LISTEN}; port 0 picks a free one)")
    p.add_argument("--public-host", metavar="HOST",
                   help="the address other nodes reach this relay at; required when listening on 0.0.0.0")
    p.add_argument("--record-out", metavar="FILE",
                   help="also write the signed membership record here (replaced atomically)")
    p.add_argument("--lease", type=_positive(MAX_LEASE), default=DEFAULT_LEASE, metavar="SECONDS",
                   help=f"how long a client registration lasts unless renewed (default {DEFAULT_LEASE:g})")
    p.add_argument("--stats-every", type=_positive(), default=DEFAULT_STATS_EVERY, metavar="SECONDS",
                   help=f"interval between stats lines on stderr (default {DEFAULT_STATS_EVERY:g})")
    return p


def _write_atomically(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".relay-record-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), file=sys.stderr, flush=True)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    host, port = args.listen
    if host in _WILDCARD_HOSTS and not args.public_host:
        parser.error("--public-host is required when listening on all interfaces "
                     "(the membership record must name a reachable address)")
    try:
        identity = Identity.load(args.identity)
        allowlist = Allowlist.load(args.authorized)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    # Handlers go in before anything is announced, so a supervisor that stops
    # the relay as soon as it sees the record still gets a clean shutdown.
    stop = threading.Event()

    def on_signal(signum, frame):
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    channel = UDPChannel(host or "0.0.0.0", port)
    transport = DirectUDPTransport(identity, channel, allowlist=allowlist)
    service = RelayService(transport, allowlist=allowlist, lease=args.lease)

    bound_port = channel.address[1]
    endpoint = (Endpoint("public", args.public_host, bound_port) if args.public_host
                else Endpoint("local", host, bound_port))
    record = sign_record(identity, [endpoint], last_seen=time.time(), roles=[ROLE_RELAY])
    line = json.dumps(record, sort_keys=True) + "\n"
    if args.record_out:
        _write_atomically(args.record_out, line)
    sys.stdout.write(line)
    sys.stdout.flush()

    _log("started", did=identity.did, listen=f"{channel.address[0]}:{bound_port}",
         endpoint=f"{endpoint.kind}:{endpoint.host}:{endpoint.port}", authorized=len(allowlist))
    try:
        while not stop.wait(args.stats_every):
            _log("stats", clients=len(service.clients()), **dict(service.stats))
    finally:
        service.stop()
        channel.close()
    _log("stopped", **dict(service.stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
