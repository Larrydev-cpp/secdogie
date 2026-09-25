"""`secdogie-identity` -- manage DID signing keys.

  genkey [outfile]      generate an Ed25519 identity; write it 0600 or print it
  did <keyfile>         print the DID document (JSON) for a keyfile
  verify <keyfile> <allowlist>   check whether a keyfile's DID is authorized
  grant <issuer_key> <subject_did> --scope S   sign a capability grant (JSON to stdout)
  verify-grant <grant.json> <issuers>          verify a signed grant
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys

from . import binding as _binding
from . import capability as _capability
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


def _bind(args: argparse.Namespace) -> int:
    idn = Identity.load(args.keyfile)
    b = _binding.create_binding(
        idn, args.transport_pub, key_version=args.key_version,
        capabilities=args.cap or (), ttl=args.ttl,
    )
    print(json.dumps(b, indent=2, ensure_ascii=False))
    return 0


def _verify_binding(args: argparse.Namespace) -> int:
    with open(args.binding, encoding="utf-8") as f:
        obj = json.load(f)
    allow = Allowlist.load(args.allowlist) if args.allowlist else None
    res = _binding.verify_binding(obj, allowlist=allow)
    if res.ok:
        print(f"valid: {res.did} -> transport {res.transport_public_key} (v{res.key_version})")
        return 0
    print(f"INVALID: {res.reason}")
    return 1



def _grant(args: argparse.Namespace) -> int:
    issuer = Identity.load(args.issuer_key)
    try:
        grant = _capability.create_capability(
            issuer, args.subject_did, args.scope, ttl=args.ttl,
        )
    except ValueError as exc:
        print(f"cannot grant: {exc}", file=sys.stderr)
        print(f"grantable scopes: {', '.join(sorted(_capability.GRANTABLE_SCOPES))}", file=sys.stderr)
        return 2
    print(json.dumps(grant, indent=2, ensure_ascii=False))
    print(
        f"granted {', '.join(grant['scopes'])} to {args.subject_did} "
        f"(id {grant['capability_id']})",
        file=sys.stderr,
    )
    return 0


def _verify_grant(args: argparse.Namespace) -> int:
    with open(args.grant, encoding="utf-8") as f:
        obj = json.load(f)
    issuers = Allowlist.load(args.issuers)
    res = _capability.verify_capability(obj, issuers=issuers, subject=args.subject)
    if res.ok:
        expires = _dt.datetime.fromtimestamp(
            float(obj["expires_at"]), _dt.timezone.utc
        ).isoformat()
        print(
            f"valid: {res.issuer} -> {res.subject}: "
            f"{', '.join(res.scopes)} (expires {expires})"
        )
        return 0
    print(f"INVALID: {res.reason}")
    return 1


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

    b = sub.add_parser("bind", help="sign a DID -> transport-key binding")
    b.add_argument("keyfile", help="this DID's signing key")
    b.add_argument("transport_pub", help="the transport public key (base64 X25519, e.g. tunnel genkey's public_key)")
    b.add_argument("--key-version", type=int, default=1)
    b.add_argument("--ttl", type=float, default=None, help="seconds until it expires (default: 1 year)")
    b.add_argument("--cap", action="append", default=[], help="a capability string (repeatable)")
    b.set_defaults(fn=_bind)

    vb = sub.add_parser("verify-binding", help="verify a binding JSON (optionally against an allowlist)")
    vb.add_argument("binding", help="path to the binding JSON")
    vb.add_argument("allowlist", nargs="?", default=None)
    vb.set_defaults(fn=_verify_binding)

    gr = sub.add_parser("grant", help="sign a capability grant from an issuer to a subject node")
    gr.add_argument("issuer_key", help="the issuer (operator) DID signing key")
    gr.add_argument("subject_did", help="the node DID being granted the scopes")
    gr.add_argument("--scope", action="append", default=[], required=True,
                    help=f"a scope to grant (repeatable); grantable: {', '.join(sorted(_capability.GRANTABLE_SCOPES))}")
    gr.add_argument("--ttl", type=float, default=None, help="seconds until it expires (default: 1 day)")
    gr.set_defaults(fn=_grant)

    vg = sub.add_parser("verify-grant", help="verify a signed capability grant against a trusted-issuer allowlist")
    vg.add_argument("grant", help="path to the grant JSON")
    vg.add_argument("issuers", help="allowlist of trusted issuer DIDs")
    vg.add_argument("--subject", default=None, help="require the grant to be for this subject DID")
    vg.set_defaults(fn=_verify_grant)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)
