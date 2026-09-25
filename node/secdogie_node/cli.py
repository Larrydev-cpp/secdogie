"""`secdogie-node` -- run a node of the mesh.

  run <node.conf>      join the mesh and keep the signed journal in sync
                       (foreground; Ctrl-C stops it)
  status <node.conf>   what this node holds, read from its files (no network)

The node syncs state; it does not execute goals (use `secdogie-citadel run`).
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading

from .config import NodeConfigError, load_config


def _run(args) -> int:
    from .node import Node

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    node = Node(load_config(args.config))
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    host, port = node.address
    print(f"node {node.did}")
    print(f"listening on {host}:{port} ({'encrypted' if node.encrypted else 'signed, unencrypted'})")
    try:
        node.run(stop)
    finally:
        node.close()
    return 0


def _status(args) -> int:
    from .node import offline_status

    print(json.dumps(offline_status(load_config(args.config)), indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="secdogie-node", description="Run a node of the secdogie mesh.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="join the mesh and keep the journal in sync (foreground)")
    r.add_argument("config")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(fn=_run)
    s = sub.add_parser("status", help="what this node holds (reads its files; no network)")
    s.add_argument("config")
    s.set_defaults(fn=_status)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except NodeConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
