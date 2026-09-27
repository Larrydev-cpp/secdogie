"""`secdogie-citadel` -- inspect and drive a Citadel journal.

  verify <journal.db>                 re-derive every chain + signature
  goals  <journal.db>                 print the projected goal tree (ready / order)
  log    <journal.db>                 print events in total order
  add-goal <db> <id> --identity KEY   append a goal (title/deps)
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

_REFRESH_INTERVAL = 5.0  # seconds between re-reads of the revocation store


def _policy(args, path):
    """An allowlist file loaded revocation-aware when --masters is given."""
    if not path:
        return None
    from secdogie_identity import load_trust_policy

    return load_trust_policy(path, masters_path=getattr(args, "masters", None),
                             revocations_path=getattr(args, "revocations", None))


def _open_writable(args) -> Journal:
    from secdogie_identity import Identity

    identity = Identity.load(args.identity)
    return Journal(args.db, identity=identity, allowlist=_policy(args, getattr(args, "authorized", None)))


def _self_policy(args):
    """A policy for checking this node's OWN DID: revocation depends only on the
    records, not on any allowlist, so it needs just --masters (+ --revocations)."""
    if not getattr(args, "masters", None):
        return None
    from secdogie_identity import (
        Allowlist,
        MasterSet,
        RevocationStore,
        TrustPolicy,
        start_refresher,
    )

    store = RevocationStore(args.revocations) if args.revocations else None
    policy = TrustPolicy(Allowlist(), masters=MasterSet.load(args.masters), store=store)
    if store is not None:
        start_refresher(policy, interval=_REFRESH_INTERVAL)
    return policy


def _add_goal(args) -> int:
    from .supervisor import Supervisor

    sup = Supervisor(_open_writable(args), run_task=lambda *a, **k: (0, ""))
    sup.add_goal(args.id, title=args.title or args.id, deps=args.dep or [])
    print(f"added goal {args.id}")
    return 0


def _run(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from secdogie_identity import Identity, halt_on_self_revocation

    from .supervisor import Supervisor, agent_run_task, terminal_confirm

    # A node whose own DID the masters have revoked does no more work: it does
    # not start, and a revocation that arrives mid-run halts the supervisor.
    self_policy = _self_policy(args)
    node_did = Identity.load(args.identity).did
    if self_policy is not None and self_policy.is_revoked(node_did):
        print(f"this node's DID {node_did} is revoked; not running")
        return 0

    issuers = _policy(args, args.issuers)
    sup = Supervisor(
        _open_writable(args), run_task=agent_run_task,
        max_attempts=args.max_attempts, confirm_handler=terminal_confirm,
        issuers=issuers,
    )
    if self_policy is not None:
        self_policy.on_change(lambda newly: halt_on_self_revocation(
            newly, node_did, [lambda: sup.halt("this node's DID was revoked")]))
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
    if sup.halted:
        print("stopped: this node's DID was revoked")
        return 0
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


def _add_grant(args) -> int:
    import json as _json

    from .supervisor import Supervisor

    with open(args.grant, encoding="utf-8") as f:
        grant = _json.load(f)
    sup = Supervisor(_open_writable(args), run_task=lambda *a, **k: (0, ""),
                     issuers=_policy(args, args.issuers))
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
    from .supervisor import Supervisor

    sup = Supervisor(_open_writable(args), run_task=lambda *a, **k: (0, ""),
                     issuers=_policy(args, args.issuers))
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

    rn = sub.add_parser("run", help="run ready goals under the supervised agent loop")
    rn.add_argument("db")
    rn.add_argument("--identity", required=True, metavar="KEYFILE", help="this node's DID signing key")
    rn.add_argument("--authorized", default=None, metavar="ALLOWLIST")
    rn.add_argument("--issuers", default=None, metavar="ALLOWLIST",
                    help="trusted issuer DIDs; enables capability enforcement (fail-closed)")
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

    for parser in (ag, rn, grn, sc):
        parser.add_argument("--masters", default=None, metavar="FILE",
                            help="master set file; enables revocation for --authorized / --issuers "
                                 "(and, for run, stops this node if its own DID is revoked)")
        parser.add_argument("--revocations", default=None, metavar="FILE",
                            help="revocation store, re-read every few seconds (requires --masters)")

    args = p.parse_args(argv)
    if getattr(args, "revocations", None) and not getattr(args, "masters", None):
        print("error: --revocations needs --masters (revocations are verified against the masters)",
              file=sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
