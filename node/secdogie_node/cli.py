"""``secdogie-node``: run the resident node, or look at its journal.

    secdogie-node run --identity node.key --apps apps.allow --operators operators.allow \\
        --authorized nodes.allow --mesh mesh.allow --issuers issuers.allow --journal node.db \\
        [--bootstrap-record peer.json ...] \\
        [--candidates memory.db] [--listen 0.0.0.0:7950] \\
        [--masters masters.conf [--revocations revocations.jsonl]] \\
        [--transport-key node.tkey --app-binding app.binding.json ...] \\
        [--relay-record relay.json ...] [--rendezvous-record rendezvous.json ...]

    secdogie-node status --journal node.db --authorized nodes.allow

``run`` is a foreground process the owner starts and stops: it prints one JSON
line when it is ready (its DID, address and self-signed membership record, which
other nodes can start from with ``--bootstrap-record``), logs to stderr, and
exits 0 on SIGTERM / SIGINT. Nothing installs itself or keeps running in the
background.

Zero trust: ``--apps`` (the operator Apps' session keys), ``--operators``
(the keys whose Gate 2 signatures authorize destructive steps) and
``--authorized`` (journal authors) and ``--mesh`` (the other nodes this one
gossips membership and replicates its journal with; every one of them must
also be on ``--authorized``) are required. Without ``--issuers`` nobody
can grant this node a capability, so every mutating action is refused; only
``--insecure-dev`` turns the capability check off, with a warning. High-risk
steps always go to the operator, whatever the flags.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from secdogie_identity import (
    Allowlist,
    Identity,
    MasterSet,
    RevocationStore,
    TrustPolicy,
    halt_on_self_revocation,
    load_trust_policy,
    start_refresher,
)

log = logging.getLogger("secdogie_node")


def _hostport(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit() or not 0 <= int(port) <= 65535:
        raise argparse.ArgumentTypeError(f"expected HOST:PORT, got {value!r}")
    return host or "0.0.0.0", int(port)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="secdogie-node", description="The resident secdogie node.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the node in the foreground")
    r.add_argument("--identity", required=True, metavar="KEYFILE", help="this node's DID signing key")
    r.add_argument("--apps", required=True, metavar="ALLOWLIST", help="operator App session keys (dialogue)")
    r.add_argument("--operators", required=True, metavar="ALLOWLIST", help="Gate 2 operator keys")
    r.add_argument("--authorized", required=True, metavar="ALLOWLIST", help="journal authors (this node included)")
    r.add_argument("--mesh", required=True, metavar="ALLOWLIST",
                   help="the other nodes: membership gossip and journal replication (each also on --authorized)")
    r.add_argument("--bootstrap-record", action="append", default=[], metavar="FILE",
                   help="a mesh node's self-signed record, as its ready line prints it (repeatable)")
    r.add_argument("--issuers", metavar="ALLOWLIST", help="capability issuers; without: every mutating action refused")
    r.add_argument("--journal", required=True, metavar="FILE", help="the node's journal (SQLite)")
    r.add_argument("--candidates", default=None, metavar="FILE", help="S2 memory quarantine (default: next to the journal)")
    r.add_argument("--listen", type=_hostport, default=("0.0.0.0", 7950), metavar="HOST:PORT")
    r.add_argument("--masters", metavar="FILE", help="master set: enables revocation of every allowlist above")
    r.add_argument("--revocations", metavar="FILE", help="revocation store, re-read every few seconds (needs --masters)")
    r.add_argument("--transport-key", metavar="FILE", help="this node's X25519 transport key (encrypts frames)")
    r.add_argument("--app-binding", action="append", default=[], metavar="FILE",
                   help="an App's signed DID -> transport-key binding (repeatable; needs --transport-key)")
    r.add_argument("--relay-record", action="append", default=[], metavar="FILE",
                   help="a relay's self-signed record, as secdogie-relay prints it (repeatable): "
                        "the fallback path when the App cannot be reached directly")
    r.add_argument("--rendezvous-record", action="append", default=[], metavar="FILE",
                   help="a rendezvous' self-signed record, as secdogie-relay --rendezvous prints it "
                        "(repeatable): this node registers there, so an App can find it by DID")
    r.add_argument("--insecure-dev", action="store_true",
                   help="INSECURE, throwaway local tests only: without --issuers, turn the capability check off")
    r.set_defaults(fn=_run)

    s = sub.add_parser("status", help="print the goals and memory recorded in a node's journal")
    s.add_argument("--journal", required=True, metavar="FILE")
    s.add_argument("--authorized", required=True, metavar="ALLOWLIST")
    s.set_defaults(fn=_status)
    return p


def _trust(args, path):
    return load_trust_policy(path, masters_path=args.masters, revocations_path=args.revocations)


def _record(text: str) -> dict:
    """A membership record from a file: the record itself, or a node's whole
    ready line (``{"event": "ready", ..., "record": {...}}``)."""
    obj = json.loads(text)
    if isinstance(obj, dict) and isinstance(obj.get("record"), dict):
        return obj["record"]
    if not isinstance(obj, dict):
        raise ValueError("a bootstrap record must be a JSON object")
    return obj


def _run(args, parser) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    if args.revocations and not args.masters:
        parser.error("--revocations needs --masters")
    if args.app_binding and not args.transport_key:
        parser.error("--app-binding needs --transport-key (bindings are for encrypted frames)")
    try:
        identity = Identity.load(args.identity)
        apps, operators, authorized, mesh = (_trust(args, f) for f in
                                             (args.apps, args.operators, args.authorized, args.mesh))
        issuers = _trust(args, args.issuers) if args.issuers else None
        tkey = None
        if args.transport_key:
            from secdogie_transport import load_transport_key

            tkey = load_transport_key(args.transport_key)
        bindings = [json.loads(Path(b).read_text(encoding="utf-8")) for b in args.app_binding]
        relays = [json.loads(Path(r).read_text(encoding="utf-8")) for r in args.relay_record]
        rendezvous = [json.loads(Path(r).read_text(encoding="utf-8")) for r in args.rendezvous_record]
        bootstrap = [_record(Path(r).read_text(encoding="utf-8")) for r in args.bootstrap_record]
    except (OSError, ValueError) as e:
        parser.error(str(e))

    self_policy = None
    if args.masters:
        store = RevocationStore(args.revocations) if args.revocations else None
        self_policy = TrustPolicy(Allowlist(), masters=MasterSet.load(args.masters), store=store)
        if self_policy.is_revoked(identity.did):
            log.warning("this node's DID %s is revoked; not starting", identity.did)
            return 0

    unrestricted = issuers is None and args.insecure_dev
    if unrestricted:
        log.warning("--insecure-dev without --issuers: NO capability check -- the agent may take any action "
                    "(high-risk steps still go to the operator)")
    elif issuers is None:
        log.warning("no --issuers: nobody can grant this node a capability -- every mutating action is refused")

    from .node import Node, NodeConfig

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    candidates = args.candidates or (args.journal + ".memory" if args.journal != ":memory:" else ":memory:")
    try:
        node = Node(NodeConfig(identity=identity, apps=apps, operators=operators, authorized=authorized, mesh=mesh,
                               issuers=issuers, unrestricted=unrestricted, journal_path=args.journal,
                               candidates_path=candidates, listen=args.listen, transport_key=tkey,
                               app_bindings=bindings, relay_records=relays,
                               rendezvous_records=rendezvous, bootstrap_records=bootstrap))
    except (OSError, ValueError) as e:
        parser.error(str(e))
    if self_policy is not None:
        self_policy.on_change(lambda newly: halt_on_self_revocation(newly, identity.did, [stop.set]))
        if args.revocations:
            start_refresher(self_policy)
    for policy in (apps, operators, authorized, mesh, issuers):
        if policy is not None and args.revocations:
            start_refresher(policy)
    node.start()
    host, port = node.address
    sys.stdout.write(json.dumps({"event": "ready", "did": identity.did, "listen": f"{host}:{port}",
                                 "record": node.record()}) + "\n")
    sys.stdout.flush()
    try:
        while not stop.wait(0.5):
            pass
    finally:
        node.stop()
    log.info("stopped")
    return 0


def _status(args, parser) -> int:
    from secdogie_citadel.consolidate import build_memory
    from secdogie_citadel.goals import build_goal_tree
    from secdogie_citadel.journal import Journal

    try:
        journal = Journal(args.journal, allowlist=Allowlist.load(args.authorized))
    except (OSError, ValueError) as e:
        parser.error(str(e))
    events = journal.events()
    tree = build_goal_tree(events)
    for gid in tree.topo_order():
        n = tree.nodes[gid]
        print(f"[{n.status:<9}] {gid}  {n.title}")
    view = build_memory(events, trust=journal.allowlist)
    print(f"memory: {len(view.records)} active record(s)")
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.fn(args, parser)


if __name__ == "__main__":
    sys.exit(main())
