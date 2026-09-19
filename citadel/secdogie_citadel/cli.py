"""`secdogie-citadel` -- inspect a Citadel journal.

  verify <journal.db>              re-derive every chain + signature
  goals  <journal.db>             print the projected goal tree (ready / order)
  log    <journal.db>             print events in total order

Appending events is done by the supervisor (a later slice) and by nodes over the
fleet transport; this CLI is read-only inspection.
"""
from __future__ import annotations

import argparse
import sys

from .goals import build_goal_tree
from .journal import Journal


def _verify(args) -> int:
    ok, reason = Journal(args.db).verify()
    print("ok" if ok else f"BROKEN: {reason}")
    return 0 if ok else 1


def _goals(args) -> int:
    tree = build_goal_tree(Journal(args.db).events())
    if tree.has_cycle():
        print("goal graph has a cycle")
        return 1
    ready = set(tree.ready())
    for gid in tree.topo_order():
        node = tree.nodes[gid]
        mark = "*" if gid in ready else " "
        print(f"{mark} [{node.status:<7}] {gid}  {node.title}")
    return 0


def _log(args) -> int:
    for e in Journal(args.db).events():
        print(f"{e['lamport']:>5} {e['author'][:16]}#{e['seq']:<4} {e['kind']}: {e['body']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="secdogie-citadel", description="Inspect a Citadel journal.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("verify", _verify), ("goals", _goals), ("log", _log)):
        s = sub.add_parser(name)
        s.add_argument("db", help="path to the journal SQLite file")
        s.set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
