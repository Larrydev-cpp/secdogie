"""``secdogie-node``: run the resident node, or look at its journal.

    secdogie-node run --identity node.key --apps apps.allow --operators operators.allow \\
        --authorized nodes.allow --issuers issuers.allow --journal node.db \\
        [--candidates memory.db] [--listen 0.0.0.0:7950] \\
        [--masters masters.conf [--revocations revocations.jsonl]] \\
        [--transport-key node.tkey --app-binding app.binding.json ...] \\
        [--relay-record relay.json ...] [--rendezvous-record rendezvous.json ...] \\
        [--webrtc-signal wss://<gateway>/ws [--webrtc-origin https://<ui>] [--webrtc-ice stun:... ...]]

    secdogie-node pair --identity node.key --apps apps.allow --operators operators.allow \\
        --webrtc-signal wss://<gateway>/ws --ui https://<ui>/ [--ttl 600]

    secdogie-node status --journal node.db --authorized nodes.allow

``run`` is a foreground process the owner starts and stops (or runs from a
service unit the owner writes -- see the README): it prints one JSON line when
it is ready (its DID and address), logs to stderr, and exits 0 on SIGTERM /
SIGINT. Nothing installs itself or keeps running in the background. With
``--webrtc-signal`` it also keeps a room on the signaling gateway, so the
operator page reaches it over a WebRTC data channel; it re-reads ``--apps`` and
``--operators`` when they change, so a browser that ``pair`` just enrolled is
heard without a restart.

``pair`` lets one browser in: it writes a one-time link to this terminal (never
to stdout or a log), shows a check code, and enrolls the browser only when you
answer "y" here *and* the page is tapped. It needs a terminal and exits when
the pairing is done, refused or expired.

Zero trust: ``--apps`` (the operator Apps' session keys), ``--operators``
(the keys whose Gate 2 signatures authorize destructive steps) and
``--authorized`` (journal authors) are required. Without ``--issuers`` nobody
can grant this node a capability, so every mutating action is refused; only
``--insecure-dev`` turns the capability check off, with a warning. High-risk
steps always go to the operator, whatever the flags.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
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
    _webrtc_flags(r, required=False)
    r.set_defaults(fn=_run)

    pr = sub.add_parser("pair", help="let one browser in, confirmed on this terminal (needs a terminal)")
    pr.add_argument("--identity", required=True, metavar="KEYFILE", help="this node's DID signing key")
    pr.add_argument("--apps", required=True, metavar="ALLOWLIST", help="where the browser's App key is added")
    pr.add_argument("--operators", required=True, metavar="ALLOWLIST",
                    help="where its operator key is added, if you allow it")
    _webrtc_flags(pr, required=True)
    pr.add_argument("--ui", required=True, metavar="URL", help="where the operator page is served")
    pr.add_argument("--ttl", type=int, default=600, metavar="SECONDS", help="how long the link works (max 3600)")
    pr.set_defaults(fn=_pair)

    s = sub.add_parser("status", help="print the goals and memory recorded in a node's journal")
    s.add_argument("--journal", required=True, metavar="FILE")
    s.add_argument("--authorized", required=True, metavar="ALLOWLIST")
    s.set_defaults(fn=_status)
    return p


def _webrtc_flags(p, *, required: bool) -> None:
    p.add_argument("--webrtc-signal", required=required, metavar="URL",
                   help="the signaling gateway (wss://.../ws; ws:// only to localhost): the browser link")
    p.add_argument("--webrtc-origin", metavar="ORIGIN",
                   help="the Origin to present to the gateway (its ALLOWED_ORIGINS), e.g. the page's origin")
    p.add_argument("--webrtc-ice", action="append", default=[], metavar="URL",
                   help="an ICE server for this node's side (repeatable; default: Cloudflare's STUN)")
    p.add_argument("--webrtc-room-epoch", type=int, default=0, metavar="N",
                   help="bump to move to a new room (every paired browser then pairs again)")


def _webrtc_config(args, identity, room: str | None = None, **kw):
    try:
        from secdogie_transport.webrtc import DEFAULT_ICE_SERVERS, WebRTCConfig
    except ImportError as e:
        raise ValueError("the browser link needs the [webrtc] extra: pip install 'secdogie-node[webrtc]'") from e
    from secdogie_identity.linkauth import derive_room

    return WebRTCConfig(args.webrtc_signal, room or derive_room(identity, args.webrtc_room_epoch),
                        origin=args.webrtc_origin, ice_servers=tuple(args.webrtc_ice) or DEFAULT_ICE_SERVERS, **kw)


def _trust(args, path):
    return load_trust_policy(path, masters_path=args.masters, revocations_path=args.revocations)


def _run(args, parser) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    if args.revocations and not args.masters:
        parser.error("--revocations needs --masters")
    if args.app_binding and not args.transport_key:
        parser.error("--app-binding needs --transport-key (bindings are for encrypted frames)")
    try:
        identity = Identity.load(args.identity)
        apps, operators, authorized = (_trust(args, f) for f in (args.apps, args.operators, args.authorized))
        issuers = _trust(args, args.issuers) if args.issuers else None
        tkey = None
        if args.transport_key:
            from secdogie_transport import load_transport_key

            tkey = load_transport_key(args.transport_key)
        bindings = [json.loads(Path(b).read_text(encoding="utf-8")) for b in args.app_binding]
        relays = [json.loads(Path(r).read_text(encoding="utf-8")) for r in args.relay_record]
        rendezvous = [json.loads(Path(r).read_text(encoding="utf-8")) for r in args.rendezvous_record]
        webrtc = _webrtc_config(args, identity) if args.webrtc_signal else None
    except (OSError, ValueError) as e:
        parser.error(str(e))
    if not args.webrtc_signal and (args.webrtc_origin or args.webrtc_ice or args.webrtc_room_epoch):
        parser.error("--webrtc-origin / --webrtc-ice / --webrtc-room-epoch need --webrtc-signal")

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
        node = Node(NodeConfig(identity=identity, apps=apps, operators=operators, authorized=authorized,
                               issuers=issuers, unrestricted=unrestricted, journal_path=args.journal,
                               candidates_path=candidates, listen=args.listen, transport_key=tkey,
                               app_bindings=bindings, relay_records=relays,
                               rendezvous_records=rendezvous, webrtc=webrtc))
    except (OSError, ValueError) as e:
        parser.error(str(e))
    _watch_allowlists(node, {"apps": (args.apps, apps), "operators": (args.operators, operators)})
    if self_policy is not None:
        self_policy.on_change(lambda newly: halt_on_self_revocation(newly, identity.did, [stop.set]))
        if args.revocations:
            start_refresher(self_policy)
    for policy in (apps, operators, authorized, issuers):
        if policy is not None and args.revocations:
            start_refresher(policy)
    node.start()
    host, port = node.address
    ready = {"event": "ready", "did": identity.did, "listen": f"{host}:{port}"}
    if webrtc is not None:
        ready["webrtc"] = True  # the room itself is never printed: it is how the page finds this node
    sys.stdout.write(json.dumps(ready) + "\n")
    sys.stdout.flush()
    try:
        while not stop.wait(0.5):
            pass
    finally:
        node.stop()
    log.info("stopped")
    return 0


def _watch_allowlists(node, lists) -> None:
    """Follow --apps / --operators on disk: an App that ``pair`` (another
    process) enrolled is heard without a restart, and every change is recorded
    in the node's journal."""
    from secdogie_identity import AllowlistWatcher

    for name, (path, target) in lists.items():
        def changed(added, removed, _name=name):
            log.warning("%s changed on disk: added %s, removed %s", _name, sorted(added) or "none",
                        sorted(removed) or "none")
            try:
                node.journal.append("enrollment", {"op": "allowlist-reload", "list": _name,
                                                   "added": sorted(added), "removed": sorted(removed)})
            except Exception:  # noqa: BLE001 - the change already applied; a journal hiccup must not undo it
                log.exception("could not journal the %s change", _name)

        AllowlistWatcher(path, target, on_change=changed).start()


def _pair(args, parser) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    from secdogie_identity.linkauth import derive_room

    from .pairing import MAX_TTL, PairingOffer, PairingPolicy, Terminal, file_enroller

    if not 0 < args.ttl <= MAX_TTL:
        parser.error(f"--ttl must be between 1 and {MAX_TTL} seconds")
    for path in (args.apps, args.operators):
        target = path if os.path.exists(path) else (os.path.dirname(os.path.abspath(path)) or ".")
        if not os.access(target, os.W_OK):
            parser.error(f"{path} is not writable: pairing appends the browser's keys to it")
    try:
        tty = Terminal()
    except OSError:
        parser.error("pairing needs a terminal: run `secdogie-node pair` in a terminal on the node's machine "
                     "(the link is a key, so it is never written to stdout or a log)")
    try:
        identity = Identity.load(args.identity)
        offer = PairingOffer(ttl=float(args.ttl))
        enroll = file_enroller(args.apps, args.operators, pairing_id=offer.pairing_id)
        from secdogie_transport.webrtc import WebRTCChannel

        policy = PairingPolicy(identity, offer, standing_room=derive_room(identity, args.webrtc_room_epoch),
                               ask=tty.ask, enroll=enroll, say=tty.say)
        channel = WebRTCChannel(_webrtc_config(args, identity, room=offer.room, bind_timeout=float(args.ttl)), policy)
    except (OSError, ValueError, ImportError) as e:
        tty.close()
        parser.error(str(e))
    channel.start(lambda data, link_id: None)
    channel.open()
    minutes = max(1, args.ttl // 60)
    tty.say(f"在要配对的浏览器里打开这个链接（{minutes} 分钟内有效，只能用一次，别发给别人）：\n"
            f"Open this link in the browser to pair ({minutes} min, single use, keep it to yourself):\n\n"
            f"  {offer.link(args.ui, identity.did)}\n")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        deadline = offer.expires_at
        while not policy.done.wait(0.5) and not stop.is_set() and time.time() < deadline:
            pass
        time.sleep(1.2)  # let the receipt go out before the link closes
    finally:
        channel.close()
    if policy.result is None:
        tty.say("没有配对。 Not paired." + (f" ({policy.refused})" if policy.refused else " (expired)"))
        tty.close()
        return 1
    tty.close()
    sys.stdout.write(json.dumps({"event": "paired", "app": policy.result.app_did,
                                 "operator": policy.result.operator_did}) + "\n")
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
