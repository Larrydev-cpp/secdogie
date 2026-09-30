"""A standalone, unattended relay node: ``secdogie-relay`` (2C.1).

`RelayService` makes relaying a role any allowlisted node can take on; this is
the process that lets a headless machine -- a VPS, a NAS -- take it on by
itself, with nobody at the keyboard::

    secdogie-relay --identity relay.key --authorized mesh.allow \\
                   --listen 0.0.0.0:7946 --public-host relay.example.net

It binds one UDP socket, serves the relay role on it, prints its self-signed
membership record (``roles=["relay"]``) as one JSON line so other nodes can be
bootstrapped with it, logs a ``stats`` line to stderr every so often, and runs
until SIGTERM / SIGINT, when it stops serving and exits 0. With ``--rendezvous``
it also serves the rendezvous role on the same socket (``roles=["relay",
"rendezvous"]``): the same allowlisted DIDs register their endpoints with it and
look each other up by DID.

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

from secdogie_identity import (
    Allowlist,
    Identity,
    MasterSet,
    RevocationStore,
    TrustPolicy,
    halt_on_self_revocation,
    start_refresher,
)

from .endpoint import Endpoint
from .membership import DEVICE_HEADLESS, ROLE_RELAY, ROLE_RENDEZVOUS, sign_record
from .relay import DEFAULT_LEASE, MAX_LEASE, RelayService
from .rendezvous import RendezvousService
from .revocation_gossip import RevocationGossip
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
    p.add_argument("--masters", metavar="FILE",
                   help="master set file; enables revocation (a revoked DID stops being served, "
                        "and this relay halts cleanly if its own DID is revoked)")
    p.add_argument("--revocations", metavar="FILE",
                   help="persist accepted revocations here so a restart still honors them "
                        "(requires --masters)")
    p.add_argument("--lease", type=_positive(MAX_LEASE), default=DEFAULT_LEASE, metavar="SECONDS",
                   help=f"how long a client registration lasts unless renewed (default {DEFAULT_LEASE:g})")
    p.add_argument("--rendezvous", action="store_true",
                   help="also serve the rendezvous role: the same DIDs register and look each other up")
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


_log_lock = threading.Lock()


def _log(event: str, **fields) -> None:
    # The stats loop and the revocation-handling thread both log, so write each
    # JSONL record (line included) in one locked write: two threads must never
    # interleave into a single unparseable line.
    line = json.dumps({"event": event, **fields}, sort_keys=True) + "\n"
    with _log_lock:
        sys.stderr.write(line)
        sys.stderr.flush()


def _stats(service: RelayService, rendezvous: RendezvousService | None) -> dict:
    stats = dict(service.stats)
    if rendezvous is not None:
        stats.update({f"rendezvous_{k}": v for k, v in rendezvous.stats.items()})
    return stats


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    host, port = args.listen
    if host in _WILDCARD_HOSTS and not args.public_host:
        parser.error("--public-host is required when listening on all interfaces "
                     "(the membership record must name a reachable address)")
    if args.revocations and not args.masters:
        parser.error("--revocations requires --masters")
    try:
        identity = Identity.load(args.identity)
        allowlist = Allowlist.load(args.authorized)
        masters = MasterSet.load(args.masters) if args.masters else None
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    # With masters configured the relay serves through a TrustPolicy (allowlist
    # minus revoked), so a revoked DID stops being served the moment a valid
    # revocation arrives -- no separate check needed.
    if masters is not None:
        store = RevocationStore(args.revocations) if args.revocations else None
        served = TrustPolicy(allowlist, masters=masters, store=store)
        if served.is_revoked(identity.did):
            _log("halted", did=identity.did, reason="own DID already revoked; not starting")
            return 0
    else:
        served = allowlist

    # Handlers go in before anything is announced, so a supervisor that stops
    # the relay as soon as it sees the record still gets a clean shutdown.
    stop = threading.Event()

    def on_signal(signum, frame):
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    channel = UDPChannel(host or "0.0.0.0", port)
    transport = DirectUDPTransport(identity, channel, allowlist=served)
    service = RelayService(transport, allowlist=served, lease=args.lease)
    rendezvous = RendezvousService(transport, allowlist=served) if args.rendezvous else None
    services = [service] + ([rendezvous] if rendezvous is not None else [])

    # Revocation: accept gossiped records, and halt cleanly if this relay's own
    # DID is revoked -- the same wind-down as an operator stopping it locally.
    if masters is not None:
        RevocationGossip(transport, served)

        def on_revocation(newly):
            if halt_on_self_revocation(newly, identity.did, [*(svc.stop for svc in services), stop.set]):
                _log("halted", did=identity.did, reason="own DID revoked")

        served.on_change(on_revocation)
        if args.revocations:
            start_refresher(served)  # after subscribing, so no record slips past the halt

    bound_port = channel.address[1]
    endpoint = (Endpoint("public", args.public_host, bound_port) if args.public_host
                else Endpoint("local", host, bound_port))
    roles = [ROLE_RELAY] + ([ROLE_RENDEZVOUS] if rendezvous is not None else [])
    record = sign_record(identity, [endpoint], last_seen=time.time(), roles=roles, device_class=DEVICE_HEADLESS)
    line = json.dumps(record, sort_keys=True) + "\n"
    if args.record_out:
        _write_atomically(args.record_out, line)
    sys.stdout.write(line)
    sys.stdout.flush()

    _log("started", did=identity.did, listen=f"{channel.address[0]}:{bound_port}",
         endpoint=f"{endpoint.kind}:{endpoint.host}:{endpoint.port}", authorized=len(allowlist))
    try:
        while not stop.wait(args.stats_every):
            _log("stats", clients=len(service.clients()), **_stats(service, rendezvous))
    finally:
        for svc in services:
            svc.stop()
        channel.close()
    _log("stopped", **_stats(service, rendezvous))
    return 0


if __name__ == "__main__":
    sys.exit(main())
