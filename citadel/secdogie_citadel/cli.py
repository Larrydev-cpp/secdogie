"""`secdogie-citadel` -- inspect and drive a Citadel journal.

  verify <journal.db>                 re-derive every chain + signature
  goals  <journal.db>                 print the projected goal tree (ready / order)
  log    <journal.db>                 print events in total order
  add-goal <db> <id> --identity KEY   append a goal (title/deps)
  set-goal <db> <id> --identity KEY --title T
                                      edit a goal's instruction and re-queue it
  run <db> --identity KEY [--issuers ALLOWLIST]
                                      run ready goals under the supervised agent loop
                                      (high-risk steps prompt on the terminal; with
                                      --issuers, every action is checked against the
                                      node's signed capability grants)
  add-grant <db> <grant.json> --identity KEY
                                      carry a signed capability grant in the journal
  scopes <db> --identity KEY --issuers ALLOWLIST
                                      print the scopes this node currently holds

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
    from secdogie_identity import Allowlist

    from .supervisor import Supervisor, agent_run_task, terminal_confirm

    issuers = Allowlist.load(args.issuers) if args.issuers else None
    sup = Supervisor(
        _open_writable(args), run_task=agent_run_task,
        max_attempts=args.max_attempts, confirm_handler=terminal_confirm,
        issuers=issuers, dib_pid=args.dib_pid,
    )
    if issuers is not None:
        scopes = sorted(sup.node_scopes())
        print("capability enforcement on: " + (", ".join(scopes) if scopes
              else "(no scopes granted -- every mutating action will be refused)"))
    requeued = sup.recover()
    if requeued:
        print(f"resumed {len(requeued)} interrupted goal(s): {', '.join(requeued)}")
    results = sup.run_ready(max_goals=args.max_goals)
    for gid, code, summary in results:
        print(f"{gid}: exit {code} -- {summary}")
    from .supervisor import DECOMPOSED

    return 1 if any(code not in (0, DECOMPOSED) for _g, code, _s in results) else 0


def _set_goal(args) -> int:
    """Edit a goal's instruction and put it back in the queue (e.g. one the
    Socratic step marked needs_input)."""
    journal = _open_writable(args)
    if args.id not in build_goal_tree(journal.events()).nodes:
        print(f"no such goal {args.id!r}")
        return 1
    journal.append("goal", {"op": "update", "id": args.id, "title": args.title, "status": "pending"})
    print(f"updated goal {args.id}")
    return 0


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


def _add_grant(args) -> int:
    import json as _json

    from secdogie_identity import Allowlist

    from .supervisor import Supervisor

    with open(args.grant, encoding="utf-8") as f:
        grant = _json.load(f)
    sup = Supervisor(_open_writable(args), run_task=lambda *a, **k: (0, ""),
                     issuers=Allowlist.load(args.issuers) if args.issuers else None)
    if args.issuers:
        from secdogie_identity.capability import verify_capability
        node_did = getattr(sup.journal, "identity", None)
        subject = node_did.did if node_did is not None else None
        res = verify_capability(grant, issuers=sup.issuers, subject=subject)
        if not res.ok:
            print(f"refusing to add grant: {res.reason}")
            return 1
    ev = sup.add_grant(grant)
    print(f"added grant {grant.get('capability_id', '?')} ({ev['kind']})")
    return 0


def _scopes(args) -> int:
    from secdogie_identity import Allowlist

    from .supervisor import Supervisor

    sup = Supervisor(_open_writable(args), run_task=lambda *a, **k: (0, ""),
                     issuers=Allowlist.load(args.issuers))
    scopes = sorted(sup.node_scopes())
    if scopes:
        for sc in scopes:
            print(sc)
    else:
        print("(no valid grants -- every mutating action will be refused)")
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

    sg = sub.add_parser("set-goal", help="edit a goal's instruction and put it back in the queue")
    sg.add_argument("db")
    sg.add_argument("id")
    sg.add_argument("--identity", required=True, metavar="KEYFILE", help="this node's DID signing key")
    sg.add_argument("--authorized", default=None, metavar="ALLOWLIST")
    sg.add_argument("--title", required=True, help="the new instruction")
    sg.set_defaults(fn=_set_goal)

    rn = sub.add_parser("run", help="run ready goals under the supervised agent loop")
    rn.add_argument("db")
    rn.add_argument("--identity", required=True, metavar="KEYFILE", help="this node's DID signing key")
    rn.add_argument("--authorized", default=None, metavar="ALLOWLIST")
    rn.add_argument("--issuers", default=None, metavar="ALLOWLIST",
                    help="trusted issuer DIDs; enables capability enforcement (fail-closed)")
    rn.add_argument("--dib-pid", type=int, default=None, metavar="PID",
                    help="also read this process's in-memory bitmaps each step (read-only, "
                         "via native atlas_inspect; Windows/Linux)")
    rn.add_argument("--max-goals", type=int, default=1000)
    rn.add_argument("--max-attempts", type=int, default=1)
    rn.set_defaults(fn=_run)

    grn = sub.add_parser("add-grant", help="carry a signed capability grant in the journal")
    grn.add_argument("db")
    grn.add_argument("grant", help="path to the signed grant JSON")
    grn.add_argument("--identity", required=True, metavar="KEYFILE", help="this node's DID signing key")
    grn.add_argument("--authorized", default=None, metavar="ALLOWLIST")
    grn.add_argument("--issuers", default=None, metavar="ALLOWLIST",
                     help="if given, verify the grant is for this node from a trusted issuer before writing")
    grn.set_defaults(fn=_add_grant)

    sc = sub.add_parser("scopes", help="print the capability scopes this node currently holds")
    sc.add_argument("db")
    sc.add_argument("--identity", required=True, metavar="KEYFILE", help="this node's DID signing key")
    sc.add_argument("--authorized", default=None, metavar="ALLOWLIST")
    sc.add_argument("--issuers", required=True, metavar="ALLOWLIST", help="trusted issuer DIDs")
    sc.set_defaults(fn=_scopes)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
