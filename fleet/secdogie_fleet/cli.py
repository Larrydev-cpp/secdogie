"""`secdogie-fleet` -- run the coordinator on the host, or a node in each desktop.

    # on the host (VM host / the box the guests can reach)
    secdogie-fleet coordinator --port 47810 --task "tidy the downloads folder" \
                               --task "check for updates" --auto

    # inside each Windows VM / session
    secdogie-fleet node --connect 192.168.56.1:47810 --label win11-vm-1

See fleet/README.md for deployment and the honest limits (Windows edition
licensing, per-guest resources, and the API quota a fleet multiplies).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from . import node as node_mod
from .server import FleetServer


def _setup_logging(verbose: bool) -> logging.Logger:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return logging.getLogger("secdogie_fleet")


def _run_coordinator(args) -> int:
    log = _setup_logging(args.verbose)
    server = FleetServer(
        host=args.host, port=args.port,
        max_concurrent=args.max_concurrent, max_attempts=args.max_attempts,
        logger=log,
    )
    server.start()
    log.info("waiting for nodes to dial in on %s:%d", *server.address)

    options = {"auto": args.auto, "dry_run": args.dry_run}
    if args.max_steps is not None:
        options["max_steps"] = args.max_steps
    if args.desktop_ax:
        options["desktop_ax"] = True
    for task in args.task:
        tid = server.submit(task, options)
        log.info("queued %s: %s", tid, task)

    if not args.task:
        log.info("no --task given; nodes will register and idle (Ctrl-C to stop)")

    try:
        while True:
            time.sleep(args.poll)
            snap = server.snapshot()
            _print_snapshot(snap)
            if args.task and snap["settled"] and snap["nodes"]:
                log.info("all tasks settled")
                break
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        server.shutdown()

    failed = [t for t in server.snapshot()["tasks"] if t["state"] == "failed"]
    return 1 if failed else 0


def _print_snapshot(snap: dict) -> None:
    nodes = ", ".join(
        f"{n['label'] or n['node_id']}{'*' if n['task_id'] else ''}" for n in snap["nodes"]
    ) or "(none)"
    print(f"nodes: {nodes}")
    for t in snap["tasks"]:
        where = f" on {t['node_id']}" if t["node_id"] else ""
        extra = f" step {t['step']}" if t["state"] == "running" and t["step"] else ""
        detail = f" -- {t['detail']}" if t["detail"] else ""
        print(f"  [{t['state']:<7}] {t['task_id']}{where}{extra}: {t['task']}{detail}")


def _run_node(args) -> int:
    log = _setup_logging(args.verbose)
    if ":" not in args.connect:
        print("error: --connect must be HOST:PORT", file=sys.stderr)
        return 2
    host, _, port_s = args.connect.rpartition(":")
    try:
        port = int(port_s)
    except ValueError:
        print(f"error: bad port in --connect {args.connect!r}", file=sys.stderr)
        return 2

    node_id = args.node_id or node_mod.default_node_id()
    delay = 1.0
    while True:
        try:
            node_mod.connect_and_serve(host, port, node_id=node_id, label=args.label, logger=log)
            delay = 1.0  # a clean disconnect resets the backoff
        except (OSError, ConnectionError) as e:
            log.warning("cannot reach coordinator %s:%d (%s)", host, port, e)
        except KeyboardInterrupt:
            log.info("interrupted")
            return 0
        if args.once:
            return 0
        log.info("reconnecting in %.0fs", delay)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            return 0
        delay = min(delay * 2, 30.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-fleet",
        description="Run one secdogie-agent per isolated desktop (a VM or its own user session) "
        "and coordinate them from the host, so several tasks genuinely act at the same time.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("coordinator", help="run the coordinator (on the host)")
    c.add_argument("--host", default="0.0.0.0", help="address to bind (default: all interfaces)")
    c.add_argument("--port", type=int, default=47810, help="port nodes dial in to (default: 47810)")
    c.add_argument("--task", action="append", default=[], metavar="TEXT",
                   help="a task to queue (repeatable); one desktop takes each")
    c.add_argument("--auto", action="store_true",
                   help="run tasks without per-step confirmation (a node has no one sitting at it "
                        "to answer prompts; high-risk actions still fail closed)")
    c.add_argument("--dry-run", action="store_true", help="nodes log what they would do, touching nothing")
    c.add_argument("--max-steps", type=int, default=None, help="step budget per task")
    c.add_argument("--desktop-ax", action="store_true",
                   help="ask nodes to run element-aware (needs the platform a11y library on each)")
    c.add_argument("--max-concurrent", type=int, default=None,
                   help="cap tasks running at once across the whole fleet, even if more desktops are "
                        "free -- the real ceiling is usually the shared API quota, not the VMs")
    c.add_argument("--max-attempts", type=int, default=2,
                   help="how many desktops may try a task before it's marked failed (default: 2)")
    c.add_argument("--poll", type=float, default=5.0, help="seconds between status printouts")
    c.set_defaults(func=_run_coordinator)

    n = sub.add_parser("node", help="run a node (inside each VM / session)")
    n.add_argument("--connect", required=True, metavar="HOST:PORT", help="the coordinator's address")
    n.add_argument("--label", default="", help="human-readable name for this desktop, e.g. win11-vm-1")
    n.add_argument("--node-id", default=None, help="stable id (default: hostname + random suffix)")
    n.add_argument("--once", action="store_true", help="don't reconnect after the coordinator goes away")
    n.set_defaults(func=_run_node)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
