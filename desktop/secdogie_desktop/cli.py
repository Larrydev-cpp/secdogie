"""`secdogie-desktop` -- a native window to control a secdogie fleet.

Runs a fleet coordinator in-process and opens a single desktop window (tkinter)
showing live nodes/tasks with submit/stop/pause/resume. On a headless host it
prints a clear message and points at secdogie-console instead.
"""
from __future__ import annotations

import argparse
import logging
import sys


def _load(identity_path, allow_path):
    if not identity_path and not allow_path:
        return None, None
    from secdogie_identity import Allowlist, Identity

    identity = Identity.load(identity_path) if identity_path else None
    allowlist = Allowlist.load(allow_path) if allow_path else None
    return identity, allowlist


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="secdogie-desktop",
        description="Native desktop window to control a secdogie fleet coordinator.",
    )
    p.add_argument("--fleet-host", default="0.0.0.0", help="address the coordinator binds for nodes")
    p.add_argument("--fleet-port", type=int, default=47810, help="port nodes dial in to")
    p.add_argument("--identity", default=None, metavar="KEYFILE", help="coordinator DID key (secure fleet)")
    p.add_argument("--authorized", default=None, metavar="ALLOWLIST", help="authorized node DIDs (secure fleet)")
    p.add_argument("--operator-key", default=None, metavar="KEYFILE",
                   help="operator DID key; the window signs its commands with it")
    p.add_argument("--operator-authorized", default=None, metavar="ALLOWLIST",
                   help="authorized operator DIDs; requires signed commands")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("secdogie_desktop")

    from secdogie_console.controller import ConsoleController
    from secdogie_fleet.server import FleetServer

    from .app import FleetWindow

    if bool(args.identity) != bool(args.authorized):
        print("error: fleet secure mode needs both --identity and --authorized", file=sys.stderr)
        return 2
    signer, node_allow = _load(args.identity, args.authorized)
    fleet = FleetServer(host=args.fleet_host, port=args.fleet_port,
                        logger=log, signer=signer, node_allowlist=node_allow)
    fleet.start()
    log.info("fleet coordinator listening on %s:%d", *fleet.address)

    operator_identity, operator_allow = _load(args.operator_key, args.operator_authorized)
    controller = ConsoleController(fleet, operator_allowlist=operator_allow)

    try:
        window = FleetWindow(controller, address=fleet.address, operator_identity=operator_identity)
    except Exception as e:
        log.error("cannot open a window (%s). A graphical desktop is required; "
                  "on a headless host use secdogie-console instead.", e)
        fleet.shutdown()
        return 4
    try:
        window.run()
    except KeyboardInterrupt:
        pass
    finally:
        fleet.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
