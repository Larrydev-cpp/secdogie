"""`secdogie-identity` -- manage DID signing keys.

  genkey [outfile]      generate an Ed25519 identity; write it 0600 or print it
  did <keyfile>         print the DID document (JSON) for a keyfile
  verify <keyfile> <allowlist>   check whether a keyfile's DID is authorized
"""
from __future__ import annotations

import argparse
import json
import sys

from . import did as _did
from .allowlist import Allowlist
from .keys import Identity


def _genkey(args: argparse.Namespace) -> int:
    idn = Identity.generate()
    if args.outfile:
        idn.save(args.outfile)
        print(f"wrote signing key to {args.outfile} (mode 0600)", file=sys.stderr)
    else:
        print(f"# did:key: {idn.did}")
        print(f"# public_key = {idn.verify_key_b64}   (share this)")
        print(f"signing_seed = {idn.seed_b64}")
    print(f"did         = {idn.did}", file=sys.stderr)
    print(f"public_key  = {idn.verify_key_b64}   (share this with authorized peers)", file=sys.stderr)
    return 0


def _did_cmd(args: argparse.Namespace) -> int:
    idn = Identity.load(args.keyfile)
    print(json.dumps(_did.did_document(idn.did), indent=2, ensure_ascii=False))
    return 0


def _verify(args: argparse.Namespace) -> int:
    idn = Identity.load(args.keyfile)
    allow = Allowlist.load(args.allowlist)
    ok = allow.contains(idn.did)
    print(f"{idn.did}: {'authorized' if ok else 'NOT authorized'}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="secdogie-identity", description="Manage secdogie DID signing keys.")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("genkey", help="generate an Ed25519 DID identity")
    g.add_argument("outfile", nargs="?", help="write the key here (0600); omit to print to stdout")
    g.set_defaults(fn=_genkey)

    d = sub.add_parser("did", help="print the DID document for a keyfile")
    d.add_argument("keyfile")
    d.set_defaults(fn=_did_cmd)

    v = sub.add_parser("verify", help="check a keyfile's DID against an allowlist")
    v.add_argument("keyfile")
    v.add_argument("allowlist")
    v.set_defaults(fn=_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)
