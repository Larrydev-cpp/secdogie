"""`secdogie-console` -- a local web control console for a fleet coordinator.

Runs a fleet coordinator and a 127.0.0.1 web UI over it, so an operator can
watch nodes/tasks and submit/stop/pause/resume work from the browser. Pair with
the fleet secure path (--identity / --authorized) to run a DID-authenticated
coordinator, and --operator-authorized to require operator-DID-signed commands
(needed before exposing the UI beyond loopback via cloudflared + Access).
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser

from .controller import ConsoleController
from .server import build_server


def _load_identity_and_allowlist(identity_path, allow_path):
    if not identity_path and not allow_path:
        return None, None
    from secdogie_identity import Allowlist, Identity

    identity = Identity.load(identity_path) if identity_path else None
    allowlist = Allowlist.load(allow_path) if allow_path else None
    return identity, allowlist


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-console",
        description="Local web control console for a secdogie fleet coordinator.",
    )
    parser.add_argument("--port", type=int, default=0, help="web UI port (default: a free one)")
    parser.add_argument("--fleet-host", default="0.0.0.0", help="address the coordinator binds for nodes")
    parser.add_argument("--fleet-port", type=int, default=47810, help="port nodes dial in to")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser")
    parser.add_argument("--identity", default=None, metavar="KEYFILE",
                        help="coordinator DID signing key (fleet secure mode)")
    parser.add_argument("--authorized", default=None, metavar="ALLOWLIST",
                        help="authorized node DIDs (fleet secure mode)")
    parser.add_argument("--operator-authorized", default=None, metavar="ALLOWLIST",
                        help="authorized operator DIDs; requires signed console commands")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("secdogie_console")

    from secdogie_fleet.server import FleetServer

    signer, node_allowlist = _load_identity_and_allowlist(args.identity, args.authorized)
    fleet = FleetServer(
        host=args.fleet_host, port=args.fleet_port,
        logger=log, signer=signer, node_allowlist=node_allowlist,
    )
    fleet.start()
    log.info("fleet coordinator listening on %s:%d", *fleet.address)

    _, operator_allowlist = _load_identity_and_allowlist(None, args.operator_authorized)
    controller = ConsoleController(fleet, operator_allowlist=operator_allowlist)
    if operator_allowlist is not None:
        log.info("console commands require an operator DID signature (%d authorized)", len(operator_allowlist))

    server = build_server(controller, port=args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    log.info("console UI on %s", url)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="console-http")
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        server.shutdown()
        server.server_close()
        fleet.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
