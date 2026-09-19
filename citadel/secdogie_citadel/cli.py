"""`secdogie-citadel` -- inspect and drive a Citadel journal.

  verify <journal.db>                 re-derive every chain + signature
  goals  <journal.db>                 print the projected goal tree (ready / order)
  log    <journal.db>                 print events in total order
  add-goal <db> <id> --identity KEY   append a goal (title/deps)
  run <db> --identity KEY             run ready goals under the supervised agent
                                      loop (high-risk steps prompt on the terminal)

verify/goals/log are read-only; add-goal/run need this node's signing key.
"""
from __future__ import annotations

import argparse
import logging
import sys

from .goals import build_goal_tree
from .journal import Journal


def _open_writable(args) -> Journal:
    from secdogie_identity import Allowlist, Identity

    identity = Identity.load(args.identity)
    allowlist = Allowlist.load(args.authorized) if getattr(args, "authorized", None) else None
    return Journal(args.db, identity=identity, allowlist=allowlist)


def _add_goal(args) -> int:
    from .supervisor import Supervisor

    sup = Supervisor(_open_writable(args), run_task=lambda *a, **k: (0, ""))
    sup.add_goal(args.id, title=args.title or args.id, deps=args.dep or [])
    print(f"added goal {args.id}")
    return 0


def _run(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from .supervisor import Supervisor, agent_run_task, terminal_confirm

    sup = Supervisor(
        _open_writable(args), run_task=agent_run_task,
        max_attempts=args.max_attempts, confirm_handler=terminal_confirm,
    )
    requeued = sup.recover()
    if requeued:
        print(f"resumed {len(requeued)} interrupted goal(s): {', '.join(requeued)}")
    results = sup.run_ready(max_goals=args.max_goals)
    for gid, code, summary in results:
        print(f"{gid}: exit {code} -- {summary}")
    return 1 if any(code not in (0,) for _g, code, _s in results) else 0


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
    p = argparse.ArgumentParser(prog="secdogie-citadel", description="Inspect and drive a Citadel journal.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("verify", _verify), ("goals", _goals), ("log", _log)):
        s = sub.add_parser(name)
        s.add_argument("db", help="path to the journal SQLite file")
        s.set_defaults(fn=fn)

    ag = sub.add_parser("add-goal", help="append a goal to the journal")
    ag.add_argument("db")
    ag.add_argument("id")
    ag.add_argument("--identity", required=True, metavar="KEYFILE", help="this node's DID signing key")
    ag.add_argument("--authorized", default=None, metavar="ALLOWLIST")
    ag.add_argument("--title", default="")
    ag.add_argument("--dep", action="append", default=[], help="a dependency goal id (repeatable)")
    ag.set_defaults(fn=_add_goal)

    rn = sub.add_parser("run", help="run ready goals under the supervised agent loop")
    rn.add_argument("db")
    rn.add_argument("--identity", required=True, metavar="KEYFILE", help="this node's DID signing key")
    rn.add_argument("--authorized", default=None, metavar="ALLOWLIST")
    rn.add_argument("--max-goals", type=int, default=1000)
    rn.add_argument("--max-attempts", type=int, default=1)
    rn.set_defaults(fn=_run)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
