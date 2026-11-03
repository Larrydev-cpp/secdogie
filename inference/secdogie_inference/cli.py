"""``secdogie-inference``: run an inference-mesh node, or ask the mesh as a client.

    secdogie-inference run --identity node.key --trust-dir trust/ --role serve --role forward \\
        [--listen 0.0.0.0:7960] [--public-host HOST] [--bootstrap rendezvous.json ...] \\
        [--peer DID@HOST:PORT ...] [--relay-record relay.json ...] \\
        [--masters masters.conf [--revocations revocations.jsonl]]

    secdogie-inference run ... --role model --lease-key lease.x25519 --backend mypkg.upstream:call
    secdogie-inference run ... --role coordinator [--standby DID ...]
    secdogie-inference run ... --role broker --keys keys.json --assignments assignments.json

    secdogie-inference ask --identity client.key --trust-dir trust/ --entry DID [--entry DID] \\
        [--bootstrap rendezvous.json | --peer DID@HOST:PORT] --model vendor/model "question"

    secdogie-inference genkey-x25519 FILE

``run`` is a foreground process the owner starts and stops. It prints one JSON
line when ready (DID, address, roles), logs JSON lines to stderr, and exits 0 on
SIGTERM / SIGINT, or when its own DID is revoked. Nothing installs itself.

Zero trust: every trust list is required (``--trust-dir`` holds
``mesh.allow``, ``users.allow``, ``deciders.allow``, ``coordinators.allow``,
``brokers.allow`` and ``operators.allow``, or give each with its own flag).
Discovery dials only DIDs on the mesh list.
"""
from __future__ import annotations

import argparse
import base64
import importlib
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from secdogie_identity import Allowlist, Identity, MasterSet, RevocationStore, TrustPolicy, load_trust_policy

from .admission import DEFAULT_ADMISSION_TTL, AdmissionBook, RevocationFilter, make_admission
from .discovery import DiscoveryConfig
from .participant import Config
from .protocol import ROLE_BROKER, ROLE_COORDINATOR, ROLE_FORWARD, ROLE_MODEL, ROLE_SERVE, AnyOf, TrustRoots
from .runtime import ROLE_CLIENT

log = logging.getLogger("secdogie_inference")

TRUST_LISTS = ("mesh", "users", "deciders", "coordinators", "brokers", "operators")
DEFAULT_PORT = 7960
_log_lock = threading.Lock()


def _log(event: str, **fields) -> None:
    line = json.dumps({"event": event, **fields}, sort_keys=True) + "\n"
    with _log_lock:
        sys.stderr.write(line)
        sys.stderr.flush()


def _hostport(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit() or not 0 <= int(port) <= 65535:
        raise argparse.ArgumentTypeError(f"expected HOST:PORT, got {value!r}")
    return host or "0.0.0.0", int(port)


def _peer(value: str) -> tuple[str, tuple[str, int]]:
    did, sep, addr = value.rpartition("@")
    if not sep or not did.startswith("did:key:"):
        raise argparse.ArgumentTypeError(f"expected DID@HOST:PORT, got {value!r}")
    return did, _hostport(addr)


def _positive(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not number > 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {value!r}")
    return number


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--identity", required=True, metavar="KEYFILE", help="this node's Ed25519 key (secdogie-identity genkey)")
    p.add_argument("--trust-dir", metavar="DIR", help="directory holding " + ", ".join(f"{n}.allow" for n in TRUST_LISTS))
    for name in TRUST_LISTS:
        p.add_argument(f"--{name}", metavar="ALLOWLIST", help=f"the {name} trust list (overrides --trust-dir)")
    p.add_argument("--masters", metavar="FILE", help="master set: enables revocation of every trust list")
    p.add_argument("--revocations", metavar="FILE", help="revocation store, re-read periodically (needs --masters)")
    p.add_argument("--listen", type=_hostport, default=("0.0.0.0", DEFAULT_PORT), metavar="HOST:PORT")
    p.add_argument("--public-host", metavar="HOST", help="the address announced to rendezvous servers")
    p.add_argument("--peer", type=_peer, action="append", default=[], metavar="DID@HOST:PORT",
                   help="a static peer (repeatable)")
    p.add_argument("--bootstrap", action="append", default=[], metavar="RECORD",
                   help="a rendezvous record, as secdogie-relay --rendezvous prints it (repeatable): "
                        "this node registers there and finds peers by DID")
    p.add_argument("--relay-record", action="append", default=[], metavar="RECORD",
                   help="a relay record (repeatable): the fallback path when a peer is not reachable directly")
    p.add_argument("--transport-key", metavar="FILE", help="X25519 key to seal frames (needs --peer-binding)")
    p.add_argument("--peer-binding", action="append", default=[], metavar="FILE",
                   help="a peer's signed DID -> transport-key binding (repeatable)")
    p.add_argument("--heartbeat-every", type=_positive, default=Config.heartbeat_every, metavar="SECONDS",
                   help="heartbeat interval; failure detection, leg timeouts and advisories scale with it")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="secdogie-inference", description="The secdogie inference mesh.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a mesh node in the foreground")
    _common(r)
    r.add_argument("--role", action="append", choices=(ROLE_SERVE, ROLE_FORWARD, ROLE_MODEL, ROLE_COORDINATOR,
                                                         ROLE_BROKER), help="repeatable; default: forward")
    r.add_argument("--lease-key", metavar="FILE", help="model role: X25519 key that opens lease grants")
    r.add_argument("--backend", metavar="MODULE:FUNCTION",
                   help="model role: callable (model, input, secret) -> output that calls the upstream API")
    r.add_argument("--standby", action="append", default=[], metavar="DID",
                   help="coordinator role: a standby forwarder DID (repeatable)")
    r.add_argument("--keys", metavar="FILE", help='broker role: JSON {"model": "secret", ...}')
    r.add_argument("--assignments", metavar="FILE",
                   help='broker role: JSON {"models": {DID: [model, ...]}, "bindings": [binding, ...]}')
    r.add_argument("--admission", action="append", default=[], metavar="FILE",
                   help="an operator admission to publish at start (repeatable; secdogie-inference admit)")
    r.add_argument("--min-degree", type=int, default=DiscoveryConfig.min_degree)
    r.add_argument("--max-degree", type=int, default=DiscoveryConfig.max_degree)
    r.add_argument("--no-discovery", action="store_true", help="link only to --peer entries")
    r.add_argument("--stats-every", type=_positive, default=30.0, metavar="SECONDS")
    r.set_defaults(fn=_run)

    a = sub.add_parser("ask", help="sign one request, send it to the mesh and print the verified outcome")
    _common(a)
    a.add_argument("--entry", action="append", required=True, metavar="DID", help="a serving node to submit to")
    a.add_argument("--standby", action="append", default=[], metavar="DID", help="a standby forwarder for Lockdown")
    a.add_argument("--model", action="append", required=True, metavar="MODEL")
    a.add_argument("--quorum", type=int, default=1)
    a.add_argument("--scope", action="append", default=[], metavar="SCOPE", help="capability scope (action-bearing)")
    a.add_argument("--timeout", type=_positive, default=60.0, metavar="SECONDS")
    a.add_argument("text")
    a.set_defaults(fn=_ask, listen=("0.0.0.0", 0))

    ad = sub.add_parser("admit", help="sign an admission: a forward-only, expiring right to join the mesh")
    ad.add_argument("--operator-key", required=True, metavar="KEYFILE", help="an operator's Ed25519 key")
    ad.add_argument("--subject", required=True, metavar="DID", help="the DID admitted (e.g. a browser node)")
    ad.add_argument("--ttl", type=_positive, default=DEFAULT_ADMISSION_TTL, metavar="SECONDS",
                    help=f"validity (default {DEFAULT_ADMISSION_TTL:g} s, at most 30 days)")
    ad.set_defaults(fn=_admit)

    k = sub.add_parser("genkey-x25519", help="write an X25519 key file (lease or transport key)")
    k.add_argument("file")
    k.set_defaults(fn=_genkey)
    return p


# -- loading -------------------------------------------------------------------------

def _trust(args, parser) -> TrustRoots:
    paths = {}
    for name in TRUST_LISTS:
        path = getattr(args, name) or (os.path.join(args.trust_dir, f"{name}.allow") if args.trust_dir else None)
        if path is None:
            parser.error(f"the {name} trust list is required (--{name} or --trust-dir)")
        paths[name] = path
    try:
        lists = {name: load_trust_policy(path, masters_path=args.masters, revocations_path=args.revocations)
                 for name, path in paths.items()}
        # The mesh is the allowlist file plus whatever operators admit (admission.py),
        # minus anything a Master revocation names.
        book = AdmissionBook()
        admitted = book
        if args.masters:
            store = RevocationStore(args.revocations) if args.revocations else None
            admitted = RevocationFilter(book, TrustPolicy(Allowlist(), masters=MasterSet.load(args.masters),
                                                          store=store))
        lists["mesh"] = AnyOf(lists["mesh"], admitted)
        trust = TrustRoots(**lists)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    book.trust = trust
    args._admissions = book
    return trust


def _record(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8").strip().splitlines()[-1])


def _backend(spec: str):
    module, sep, name = spec.partition(":")
    if not sep or not module or not name:
        raise ValueError(f"--backend must be MODULE:FUNCTION, got {spec!r}")
    fn = getattr(importlib.import_module(module), name)
    if not callable(fn):
        raise ValueError(f"{spec} is not callable")
    return fn


def _timing(every: float) -> Config:
    """Scale every timer with the heartbeat interval (the defaults' ratios)."""
    base = Config()
    k = every / base.heartbeat_every
    return Config(heartbeat_every=every, fail_after=base.fail_after * k, leg_timeout=base.leg_timeout * k,
                  advise_every=base.advise_every * k)


def _runtime_config(args, parser, roles, trust):
    from secdogie_transport import load_transport_key

    from .runtime import RuntimeConfig

    if args.peer_binding and not args.transport_key:
        parser.error("--peer-binding needs --transport-key")
    try:
        identity = Identity.load(args.identity)
        cfg = RuntimeConfig(
            identity=identity, trust=trust, roles=frozenset(roles), listen=args.listen,
            public_host=args.public_host, peers=dict(args.peer),
            bootstrap=[_record(f) for f in args.bootstrap], relays=[_record(f) for f in args.relay_record],
            transport_key=load_transport_key(args.transport_key) if args.transport_key else None,
            peer_bindings=[json.loads(Path(f).read_text(encoding="utf-8")) for f in args.peer_binding],
            config=_timing(args.heartbeat_every), tick=min(0.2, args.heartbeat_every / 4),
            admissions=getattr(args, "_admissions", None),
            preload=[json.loads(Path(f).read_text(encoding="utf-8")) for f in getattr(args, "admission", [])])
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return cfg


def _self_halt(args, identity, stop: threading.Event) -> bool:
    """With --masters: refuse to start when this DID is already revoked, and
    stop when a revocation of it arrives. Returns False when revoked now."""
    if not args.masters:
        return True
    from secdogie_identity import halt_on_self_revocation, start_refresher

    store = RevocationStore(args.revocations) if args.revocations else None
    policy = TrustPolicy(Allowlist(), masters=MasterSet.load(args.masters), store=store)
    if policy.is_revoked(identity.did):
        _log("halted", did=identity.did, reason="own DID already revoked; not starting")
        return False

    def on_change(newly):
        if halt_on_self_revocation(newly, identity.did, [stop.set]):
            _log("halted", did=identity.did, reason="own DID revoked")

    policy.on_change(on_change)
    if args.revocations:
        start_refresher(policy)
    return True


# -- commands -------------------------------------------------------------------------

def _run(args, parser) -> int:
    from .lease import MemoryKeySource
    from .runtime import Runtime

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    roles = set(args.role or [ROLE_FORWARD])
    trust = _trust(args, parser)
    cfg = _runtime_config(args, parser, roles, trust)
    try:
        if ROLE_MODEL in roles:
            if not args.lease_key or not args.backend:
                parser.error("the model role needs --lease-key and --backend")
            from secdogie_transport import load_transport_key

            cfg.lease_key = load_transport_key(args.lease_key)
            cfg.backend = _backend(args.backend)
        if ROLE_COORDINATOR in roles:
            cfg.standby = list(args.standby)
        if ROLE_BROKER in roles:
            if not args.keys or not args.assignments:
                parser.error("the broker role needs --keys and --assignments")
            from .broker import assignments_from_bindings

            keys = json.loads(Path(args.keys).read_text(encoding="utf-8"))
            spec = json.loads(Path(args.assignments).read_text(encoding="utf-8"))
            cfg.key_source = MemoryKeySource({str(m): str(s) for m, s in keys.items()})
            cfg.assignments = assignments_from_bindings(spec.get("bindings", []), spec.get("models", {}), trust,
                                                        now=time.time())
    except (OSError, ValueError, ImportError, AttributeError) as exc:
        parser.error(str(exc))
    cfg.discovery = None if args.no_discovery else DiscoveryConfig(min_degree=args.min_degree,
                                                                   max_degree=args.max_degree)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if not _self_halt(args, cfg.identity, stop):
        return 0
    try:
        runtime = Runtime(cfg)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    runtime.start()
    host, port = runtime.address
    sys.stdout.write(json.dumps({"event": "ready", "did": runtime.did, "listen": f"{host}:{port}",
                                 "roles": sorted(roles)}) + "\n")
    sys.stdout.flush()
    _log("started", did=runtime.did, listen=f"{host}:{port}", roles=sorted(roles))
    try:
        while not stop.wait(args.stats_every):
            _log("stats", **runtime.stats())
    finally:
        runtime.stop()
    _log("stopped", **runtime.stats())
    return 0


EXIT = {"accepted": 0, "intercepted": 2, "refused": 3}


def _ask(args, parser) -> int:
    from .runtime import Runtime

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    trust = _trust(args, parser)
    cfg = _runtime_config(args, parser, {ROLE_CLIENT}, trust)
    cfg.entries, cfg.standby, cfg.discovery = list(args.entry), list(args.standby), None
    try:
        runtime = Runtime(cfg)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    runtime.start()
    try:
        deadline = time.time() + args.timeout
        targets = set(args.entry) - set(cfg.peers)
        while targets - set(runtime.resolver.found) and time.time() < deadline:
            for did in targets - set(runtime.resolver.found):
                runtime.resolver.resolve(did)
            time.sleep(0.1)
        handle = runtime.participant.ask(args.model, args.text, scopes=args.scope, quorum=args.quorum)
        outcome = None
        while outcome is None and time.time() < deadline:
            time.sleep(0.1)
            outcome = runtime.participant.take(handle)
    finally:
        runtime.stop()
    if outcome is None:
        print(json.dumps({"status": "timeout"}))
        return 4
    print(json.dumps({"status": outcome.status, "output": outcome.output, "providers": list(outcome.providers),
                      "reasons": list(outcome.reasons), "mode": outcome.mode}, ensure_ascii=False))
    return EXIT.get(outcome.status, 3)


def _admit(args, parser) -> int:
    try:
        operator = Identity.load(args.operator_key)
        admission = make_admission(operator, args.subject, ttl=args.ttl)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(admission, sort_keys=True))
    return 0


def _genkey(args, parser) -> int:
    from nacl.public import PrivateKey

    path = Path(args.file)
    if path.exists():
        parser.error(f"{path} exists; refusing to overwrite a key")
    key = PrivateKey.generate()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(f"private_key = {base64.b64encode(bytes(key)).decode()}\n")
    print(json.dumps({"public_key": base64.b64encode(bytes(key.public_key)).decode()}))
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.fn(args, parser)


if __name__ == "__main__":
    sys.exit(main())
