"""``secdogie-dialogue``: the operator's App from the command line.

    secdogie-dialogue new-operator-key OUT         # an encrypted operator key; prints its DID
    secdogie-dialogue operator-did KEYSTORE        # the DID a keystore holds

    secdogie-dialogue connect --identity app.key --node did:key:z6Mk... \\
        (--node-addr 10.0.0.5:7950 | --rendezvous-record rendezvous.json ...) \\
        [--listen 0.0.0.0:0] \\
        [--operator-keystore op.keystore] \\
        [--transport-key app.tkey --node-binding node.binding.json] \\
        [--relay-record relay.json ...] \\
        [--headless SCRIPT.jsonl|- [--passphrase-file FILE]]

``connect`` talks to exactly one node, named by DID: that DID is the whole
trust set, for the transport and for the dialogue session alike, so nothing
else is heard. With ``--relay-record`` (a relay's self-signed record, as
``secdogie-relay`` prints it) the App also reaches the node through that relay
whenever it has not heard the node directly of late. With ``--rendezvous-record``
(a rendezvous' self-signed record, as ``secdogie-relay --rendezvous`` prints it)
the App looks the node up by its DID instead of being told its address.
``--identity`` is the App's session key (it signs envelopes and
memory confirmations); the operator key stays in its keystore and is unlocked
once per Gate 2 approval.

Without ``--headless`` it opens the Textual screen (``pip install
secdogie-dialogue[tui]``). With it, it runs a script of operator steps, one
JSON object per line (see ``app.run_script``), prints one JSON result per step
and exits 0 only if every step succeeded -- for end-to-end tests. A script
approves nothing by default: each ``approve`` step names the exact action it
approves, matches one challenge, and signs once, after the same review the
screen does.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .keystore import KeystoreError, keystore_did, seal_identity, unseal_identity


def _hostport(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit() or not 0 <= int(port) <= 65535:
        raise argparse.ArgumentTypeError(f"expected HOST:PORT, got {value!r}")
    return host or "0.0.0.0", int(port)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="secdogie-dialogue",
                                description="The operator's Dialogue & Alignment App for a secdogie node.")
    sub = p.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("new-operator-key", help="create an encrypted operator (Gate 2) key; prints its DID")
    k.add_argument("out", help="keystore file to create (0600; never overwritten)")
    k.add_argument("--passphrase-file", help="read the passphrase from this file instead of prompting")
    k.set_defaults(fn=_new_operator_key)

    d = sub.add_parser("operator-did", help="print the DID an operator keystore holds")
    d.add_argument("keystore")
    d.set_defaults(fn=_operator_did)

    c = sub.add_parser("connect", help="open a dialogue session with one node")
    c.add_argument("--identity", required=True, metavar="KEYFILE", help="the App's session key")
    c.add_argument("--node", required=True, metavar="DID", help="the node's DID: the only peer trusted")
    c.add_argument("--node-addr", type=_hostport, metavar="HOST:PORT",
                   help="where the node listens (or find it with --rendezvous-record)")
    c.add_argument("--listen", type=_hostport, default=("0.0.0.0", 0), metavar="HOST:PORT")
    c.add_argument("--operator-keystore", metavar="FILE", help="the operator key, for Gate 2 approvals")
    c.add_argument("--transport-key", metavar="FILE", help="this App's X25519 transport key (encrypts frames)")
    c.add_argument("--node-binding", metavar="FILE", help="the node's signed DID -> transport-key binding")
    c.add_argument("--relay-record", action="append", default=[], metavar="FILE",
                   help="a relay's self-signed record (repeatable): the fallback path to the node")
    c.add_argument("--rendezvous-record", action="append", default=[], metavar="FILE",
                   help="a rendezvous' self-signed record (repeatable): look the node up by its DID there")
    c.add_argument("--headless", metavar="SCRIPT", help="run operator steps from a JSON-lines file ('-' = stdin)")
    c.add_argument("--passphrase-file", metavar="FILE", help="headless only: the operator passphrase")
    c.add_argument("--step-timeout", type=float, default=30.0, metavar="SECONDS")
    c.set_defaults(fn=_connect)
    return p


def _read_passphrase(path: str | None, *, confirm: bool = False) -> bytes:
    if path:
        return Path(path).read_bytes().rstrip(b"\r\n")
    first = getpass.getpass("operator passphrase: ").encode("utf-8")
    if confirm and getpass.getpass("again: ").encode("utf-8") != first:
        raise KeystoreError("the passphrases differ")
    return first


def _new_operator_key(args, parser) -> int:
    from secdogie_identity import Identity

    try:
        identity = Identity.generate()
        seal_identity(identity, _read_passphrase(args.passphrase_file, confirm=True), args.out)
    except (OSError, KeystoreError) as e:
        parser.error(str(e))
    print(identity.did)
    print(f"list this DID as an operator on the nodes this key may authorize for; keystore: {args.out}",
          file=sys.stderr)
    return 0


def _operator_did(args, parser) -> int:
    try:
        print(keystore_did(args.keystore))
    except KeystoreError as e:
        parser.error(str(e))
    return 0


def _load_script(src: str) -> list[dict]:
    text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
    steps = []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        try:
            steps.append(json.loads(s))
        except json.JSONDecodeError as e:
            raise ValueError(f"script line {n}: {e}") from None
    return steps


def _connect(args, parser) -> int:
    if not args.node_addr and not args.rendezvous_record:
        parser.error("say where the node is: --node-addr, or --rendezvous-record to look it up by DID")
    if bool(args.transport_key) != bool(args.node_binding):
        parser.error("--transport-key and --node-binding go together (encryption needs both ends' keys)")
    if args.passphrase_file and not args.headless:
        parser.error("--passphrase-file is for --headless; the screen asks for the passphrase")
    try:
        from secdogie_transport import (
            ChannelMux,
            DirectUDPTransport,
            Endpoint,
            FailoverTransport,
            PeerIdentity,
            RendezvousLink,
            Session,
            UDPChannel,
        )
        from secdogie_transport.sealed import load_transport_key
    except ImportError:
        parser.error("connect needs the transport: pip install 'secdogie-dialogue[net]'")
    from secdogie_identity import Allowlist, Identity

    from .app import AppController, run_script
    from .session import DialogueSession, SessionRouter

    steps = None
    try:
        identity = Identity.load(args.identity)
        if args.headless:
            steps = _load_script(args.headless)
        tkey = load_transport_key(args.transport_key) if args.transport_key else None
        binding = json.loads(Path(args.node_binding).read_text(encoding="utf-8")) if args.node_binding else None
        relays = [json.loads(Path(r).read_text(encoding="utf-8")) for r in args.relay_record]
        rendezvous = [json.loads(Path(r).read_text(encoding="utf-8")) for r in args.rendezvous_record]
        if args.operator_keystore:
            keystore_did(args.operator_keystore)  # fail now, not at the first approval
    except (OSError, ValueError, KeystoreError) as e:
        parser.error(str(e))

    trust = Allowlist({args.node})  # the one node, and nothing else
    channel = UDPChannel(*args.listen)
    ctl = link = finder = None
    try:
        transport = DirectUDPTransport(identity, channel, allowlist=trust, transport_key=tkey)
        if binding is not None and not (transport.add_peer_binding(binding) and binding.get("did") == args.node):
            parser.error("--node-binding is not a valid binding for --node")
        if args.node_addr:
            transport.set_peer_endpoint(args.node, *args.node_addr)
        if rendezvous:
            try:
                finder = RendezvousLink.from_records(transport, rendezvous)
            except ValueError as e:
                parser.error(str(e))
            found = finder.lookup(args.node)
            if found is not None:
                best = found.best()
                transport.set_peer_endpoint(args.node, best.host, best.port)
            elif not args.node_addr and not relays:
                sys.stderr.write(f"the node {args.node} is not registered at any rendezvous given\n")
                return 1
        carrier = transport
        if relays:
            try:
                link = carrier = FailoverTransport.from_records(transport, relays)
            except ValueError as e:
                parser.error(str(e))
            link.start()
        mux = ChannelMux(carrier, Session("dialogue-app", PeerIdentity(identity.did, ""),
                                          active=Endpoint("local", *channel.address)))
        router = SessionRouter(mux)
        session = router.add(DialogueSession(identity, args.node, router.sender_for(args.node), trust=trust))
        ctl = AppController(session)

        keystore = args.operator_keystore

        def unlock_with(passphrase: bytes):
            return unseal_identity(keystore, passphrase)

        session.start(0.05)
        ctl.start()
        if steps is not None:
            unlock = None
            if keystore and args.passphrase_file:
                pf = args.passphrase_file

                def unlock():
                    return unlock_with(_read_passphrase(pf))

            def emit(result: dict) -> None:
                sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
                sys.stdout.flush()

            return run_script(ctl, steps, unlock=unlock, emit=emit, default_timeout=args.step_timeout)
        try:
            from .tui import run_tui
        except ImportError:
            parser.error("the screen needs Textual: pip install 'secdogie-dialogue[tui]' (or use --headless)")
        run_tui(ctl, unlock_with=unlock_with if keystore else None)
        return 0
    finally:
        if ctl is not None:
            ctl.close()
        if link is not None:
            link.close()
        if finder is not None:
            finder.close()
        channel.close()


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.fn(args, parser)


if __name__ == "__main__":
    sys.exit(main())
