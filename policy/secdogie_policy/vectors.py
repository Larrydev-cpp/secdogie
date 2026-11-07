"""Golden vectors for policy bundles: fixtures/vectors/policy.json.

Python is the reference: every bundle below is built and signed by the
production functions, and every expected verdict is what ``verify_bundle``
returns. The TypeScript validator (symbiont/tests/policy.test.ts) must give the
same ``ok`` / ``errors`` / ``signers`` for every case, the same normal form for
every phrase, the same chain errors and the same floor. The seeds are public
test keys; never use them for anything real.
"""
from __future__ import annotations

import base64
import copy
import hashlib

from secdogie_identity import Allowlist, Identity, MasterSet
from secdogie_identity.signing import canonical

from .bundle import assemble, bundle_id, check_successor, sign_bundle, verify_bundle
from .floor import FLOOR
from .text import normalize_phrase


def _identity(label: str) -> tuple[Identity, str]:
    seed = hashlib.sha256(f"secdogie/vectors/policy/{label}".encode()).digest()
    return Identity.from_seed_b64(base64.b64encode(seed).decode("ascii")), seed.hex()


OPERATOR, OPERATOR_SEED = _identity("operator")
OUTSIDER, OUTSIDER_SEED = _identity("outsider")
MASTERS = [_identity(f"master-{i}") for i in range(3)]
THRESHOLD = 2
NODE, _ = _identity("node")

_PROMPT_HASH = hashlib.sha256(b"secdogie/vectors/policy/prompt-template").hexdigest()


def _text(obj) -> str:
    return canonical(obj).decode("utf-8")  # any JSON value, not only objects


def _gate_rules_body() -> dict:
    return {
        "instruction": {
            "max_len": 400,
            "max_steps": 5,
            "revise_on": [
                {"phrases": ["mass register", "批量注册"],
                 "reason": "registering accounts in bulk is not what this mesh is for",
                 "suggestion": "name the one account you own and what to do with it"},
            ],
        },
        "plan": {
            "require_intent": True,
            "enforce_capabilities": True,
            "repeat_threshold": 1,
            "poll_window": 2,
            "known_failure_min_runs": 2,
            "max_cost": 30,
            "extra_destructive_kinds": ["clear", "purge"],
            "blocked_kinds": ["run"],
            "blocked_targets": [{"name": "terminal"}, {"name": "delete all", "role": "button"}],
        },
    }


def _decision_body() -> dict:
    return {
        "policy": "llm/v1",
        "weights": {"hop_milli": 1000, "load_milli": 500},
        "deny_providers": [OUTSIDER.did],
        "llm": {"model": "router-small", "prompt_template_hash": _PROMPT_HASH, "timeout_ms": 1500,
                "max_tokens": 256},
        "rollout": {"stage": "shadow", "promote_after": 1000},
    }


def _ingest_body() -> dict:
    return {
        "origins": ["https://data.example.org", "https://docs.example.com:8443"],
        "submitters": sorted([OPERATOR.did, NODE.did]),
        "rate": {"rps_milli": 500, "concurrency": 1},
        "modes": ["rendered", "static"],
        "max_body_bytes": 2 << 20,
        "timeout_ms": 10_000,
    }


def _allowlist_body() -> dict:
    return {"add": [NODE.did], "remove": [OUTSIDER.did]}


def _base(kind: str, body: dict, **kw) -> dict:
    kw.setdefault("mesh", "home-lab")
    kw.setdefault("version", 1)
    kw.setdefault("not_before", 1_767_225_600)
    return assemble(kind, body=body, **kw)


def _sign(b: dict, *signers: Identity) -> dict:
    for s in signers:
        b = sign_bundle(s, b)
    return b


def _mutate(b: dict, fn) -> dict:
    """Change an unsigned bundle and recompute its id, so the change itself is
    what the case is about (not a stale id)."""
    out = copy.deepcopy(b)
    fn(out)
    out["bundle_id"] = bundle_id(out)
    return out


def _cases() -> list[tuple[str, dict]]:
    m0, m1, m2 = (m for m, _ in MASTERS)
    gate = _base("gate-rules", _gate_rules_body())
    dm = _base("decision-model", _decision_body())
    ing = _base("ingest", _ingest_body(), scope={"nodes": [], "roles": ["ingest"]})
    allow = _base("allowlist", _allowlist_body())

    def body(**changes):
        def fn(b):
            for path, value in changes.items():
                target = b
                keys = path.split("__")
                for k in keys[:-1]:
                    target = target[k]
                if value is _DELETE:
                    del target[keys[-1]]
                else:
                    target[keys[-1]] = value
        return fn

    cases: list[tuple[str, dict]] = [
        # accepted
        ("gate-rules signed by an operator", _sign(gate, OPERATOR)),
        ("gate-rules v2 naming v1", _sign(_base("gate-rules", {"instruction": {"max_len": 300}}, version=2,
                                                supersedes=gate["bundle_id"]), OPERATOR)),
        ("an empty gate-rules body (back to the built-ins)", _sign(_base("gate-rules", {}), OPERATOR)),
        ("decision-model signed by 2 of 3 masters", _sign(dm, m0, m2)),
        ("decision-model with all three masters", _sign(dm, m0, m1, m2)),
        ("built-in router, nothing else", _sign(_base("decision-model", {"policy": "router/v1"}), m1, m2)),
        ("canary rollout", _sign(_base("decision-model", {"policy": "weighted/v1",
                                                          "rollout": {"stage": "canary", "canary": [NODE.did]}}),
                                 m0, m1)),
        ("ingest signed by an operator", _sign(ing, OPERATOR)),
        ("allowlist signed by 2 masters", _sign(allow, m1, m0)),
        # signatures
        ("gate-rules signed by an outsider", _sign(gate, OUTSIDER)),
        ("gate-rules unsigned", gate),
        ("gate-rules signed by a master, who is not an operator", _sign(gate, m0)),
        ("decision-model with one master", _sign(dm, m1)),
        ("decision-model signed by the operator", _sign(dm, OPERATOR)),
        ("decision-model with one master signing twice", {**_sign(dm, m1),
                                                          "sigs": _sign(dm, m1)["sigs"] * 2}),
        ("a signature over other bytes", {**_sign(gate, OPERATOR),
                                          "sigs": _sign(_base("gate-rules", {}), OPERATOR)["sigs"]}),
        ("a sig that is not base64", {**gate, "sigs": [{"signer": OPERATOR.did, "sig": "%%%"}]}),
        ("sigs is not a list", {**gate, "sigs": {"signer": OPERATOR.did}}),
        ("seventeen sigs", {**gate, "sigs": [{"signer": OPERATOR.did, "sig": ""}] * 17}),
        # tampering
        ("body changed after signing, id left alone",
         {**_sign(gate, OPERATOR), "body": {"instruction": {"max_len": 600}}}),
        ("body changed after signing, id recomputed",
         _mutate(_sign(gate, OPERATOR), body(body={"instruction": {"max_len": 600}}))),
        ("bundle_id is not a string", {**gate, "bundle_id": 7}),
        # loosening and range
        ("max_len above the floor", _sign(_mutate(gate, body(body__instruction__max_len=601)), OPERATOR)),
        ("max_len zero", _sign(_mutate(gate, body(body__instruction__max_len=0)), OPERATOR)),
        ("max_len as a float", _sign(_mutate(gate, body(body__instruction__max_len=400.0)), OPERATOR)),
        ("max_len as a bool", _sign(_mutate(gate, body(body__instruction__max_len=True)), OPERATOR)),
        ("max_steps above the floor", _sign(_mutate(gate, body(body__instruction__max_steps=7)), OPERATOR)),
        ("require_intent false", _sign(_mutate(gate, body(body__plan__require_intent=False)), OPERATOR)),
        ("repeat_threshold above the floor",
         _sign(_mutate(gate, body(body__plan__repeat_threshold=3)), OPERATOR)),
        ("poll_window below its range", _sign(_mutate(gate, body(body__plan__poll_window=1)), OPERATOR)),
        ("known_failure_min_runs above the floor",
         _sign(_mutate(gate, body(body__plan__known_failure_min_runs=4)), OPERATOR)),
        ("a field that would switch a check off",
         _sign(_mutate(gate, body(body__plan__disable_checks=["known-failure"])), OPERATOR)),
        ("a field that would touch Gate 2",
         _sign(_mutate(gate, body(body__plan__require_authorization=False)), OPERATOR)),
        ("an unknown top-level field", {**gate, "comment": "hi"}),
        # phrases and lists
        ("a phrase in upper case", _sign(_mutate(gate, body(
            body__instruction__revise_on=[{"phrases": ["Mass Register"], "reason": "r"}])), OPERATOR)),
        ("a full-width phrase", _sign(_mutate(gate, body(
            body__instruction__revise_on=[{"phrases": ["ｍａｓｓ"], "reason": "r"}])), OPERATOR)),
        ("a phrase with a double space", _sign(_mutate(gate, body(
            body__instruction__revise_on=[{"phrases": ["mass  register"], "reason": "r"}])), OPERATOR)),
        ("a phrase with a tab", _sign(_mutate(gate, body(
            body__instruction__revise_on=[{"phrases": ["mass\tregister"], "reason": "r"}])), OPERATOR)),
        ("phrases out of order", _sign(_mutate(gate, body(
            body__instruction__revise_on=[{"phrases": ["b", "a"], "reason": "r"}])), OPERATOR)),
        ("phrases repeated", _sign(_mutate(gate, body(
            body__instruction__revise_on=[{"phrases": ["a", "a"], "reason": "r"}])), OPERATOR)),
        ("a revise_on entry with no reason", _sign(_mutate(gate, body(
            body__instruction__revise_on=[{"phrases": ["a"]}])), OPERATOR)),
        ("a blocked target with neither role nor name", _sign(_mutate(gate, body(
            body__plan__blocked_targets=[{}])), OPERATOR)),
        ("a kind token in upper case", _sign(_mutate(gate, body(
            body__plan__blocked_kinds=["Run"])), OPERATOR)),
        ("several errors at once", _sign(_mutate(gate, body(
            body__instruction__max_len=9999, body__instruction__extra=1, body__plan__poll_window="2",
            body__plan__blocked_kinds=["b", "a"])), OPERATOR)),
        # decision-model shape
        ("llm policy without its llm block", _sign(_mutate(dm, body(body__llm=_DELETE)), m0, m1)),
        ("router with an llm block", _sign(_mutate(dm, body(body__policy="router/v1")), m0, m1)),
        ("a policy nobody knows", _sign(_mutate(dm, body(body__policy="oracle/v1")), m0, m1)),
        ("a weighted policy without a rollout", _sign(_base("decision-model", {"policy": "weighted/v1"}), m0, m1)),
        ("canary stage with no canary nodes", _sign(_base("decision-model", {
            "policy": "weighted/v1", "rollout": {"stage": "canary"}}), m0, m1)),
        ("full stage that names canaries", _sign(_base("decision-model", {
            "policy": "weighted/v1", "rollout": {"stage": "full", "canary": [NODE.did]}}), m0, m1)),
        ("promote_after outside shadow", _sign(_base("decision-model", {
            "policy": "weighted/v1", "rollout": {"stage": "full", "promote_after": 500}}), m0, m1)),
        ("a prompt hash that is not hex", _sign(_mutate(dm, body(body__llm__prompt_template_hash="X" * 64)),
                                                m0, m1)),
        ("a deny list entry that is not a DID", _sign(_mutate(dm, body(body__deny_providers=["did:key:nope"])),
                                                      m0, m1)),
        # ingest shape
        ("an http origin", _sign(_mutate(ing, body(body__origins=["http://data.example.org"])), OPERATOR)),
        ("an origin with a path", _sign(_mutate(ing, body(body__origins=["https://a.example/x"])), OPERATOR)),
        ("an origin with the default port", _sign(_mutate(ing, body(body__origins=["https://a.example:443"])),
                                                  OPERATOR)),
        ("an origin in upper case", _sign(_mutate(ing, body(body__origins=["https://A.example"])), OPERATOR)),
        ("an origin with a port past 65535",
         _sign(_mutate(ing, body(body__origins=["https://a.example:65536"])), OPERATOR)),
        ("a single-label host", _sign(_mutate(ing, body(body__origins=["https://localhost"])), OPERATOR)),
        ("origins out of order", _sign(_mutate(ing, body(
            body__origins=["https://b.example", "https://a.example"])), OPERATOR)),
        ("faster than one request a second", _sign(_mutate(ing, body(body__rate__rps_milli=1001)), OPERATOR)),
        ("two at a time", _sign(_mutate(ing, body(body__rate__concurrency=2)), OPERATOR)),
        ("a robots switch", _sign(_mutate(ing, body(body__robots=False)), OPERATOR)),
        ("bodies past 8 MiB", _sign(_mutate(ing, body(body__max_body_bytes=(8 << 20) + 1)), OPERATOR)),
        ("a mode nobody knows", _sign(_mutate(ing, body(body__modes=["screenshot"])), OPERATOR)),
        ("no submitters", _sign(_mutate(ing, body(body__submitters=[])), OPERATOR)),
        # allowlist shape
        ("add and remove the same DID", _sign(_base("allowlist", {"add": [NODE.did], "remove": [NODE.did]}),
                                              m0, m1)),
        ("an allowlist change that changes nothing", _sign(_base("allowlist", {}), m0, m1)),
        # envelope
        ("not an object", ["a", "list"]),
        ("a kind nobody knows", _sign(_base("firmware", {"blob": "..."}), OPERATOR)),
        ("the wrong type", _sign(_mutate(gate, body(type="secdogie/policy-bundle/v2")), OPERATOR)),
        ("a bad mesh name", _sign(_base("gate-rules", {}, mesh="Home Lab"), OPERATOR)),
        ("version 0", _sign(_base("gate-rules", {}, version=0), OPERATOR)),
        ("version 2 with no predecessor named", _sign(_base("gate-rules", {}, version=2), OPERATOR)),
        ("version 1 naming a predecessor", _sign(_base("gate-rules", {}, supersedes=gate["bundle_id"]),
                                                 OPERATOR)),
        ("a role nobody knows", _sign(_base("gate-rules", {}, scope={"nodes": [], "roles": ["admin"]}),
                                      OPERATOR)),
        ("a scope without nodes", _sign(_base("gate-rules", {}, scope={"roles": []}), OPERATOR)),
        ("a missing field", {k: v for k, v in _sign(gate, OPERATOR).items() if k != "not_before"}),
    ]
    return cases


class _Delete:
    pass


_DELETE = _Delete()


def _chain() -> list[dict]:
    m0, m1, _ = (m for m, _ in MASTERS)
    v1 = _sign(_base("gate-rules", {"instruction": {"max_len": 500}}), OPERATOR)
    v2 = _sign(_base("gate-rules", {"instruction": {"max_len": 400}}, version=2, supersedes=v1["bundle_id"]),
               OPERATOR)
    v3 = _sign(_base("gate-rules", {}, version=3, supersedes=v2["bundle_id"]), OPERATOR)
    other_scope = _sign(_base("gate-rules", {}, version=2, supersedes=v1["bundle_id"],
                              scope={"nodes": [], "roles": ["serve"]}), OPERATOR)
    other_mesh = _sign(_base("gate-rules", {}, version=2, supersedes=v1["bundle_id"], mesh="office"), OPERATOR)
    other_kind = _sign(_base("allowlist", {"add": [NODE.did]}, version=2, supersedes=v1["bundle_id"]), m0, m1)
    wrong_parent = _sign(_base("gate-rules", {}, version=2, supersedes=v3["bundle_id"]), OPERATOR)
    rows = [
        ("the first bundle", None, v1),
        ("the next version naming its predecessor", v1, v2),
        ("a gap in versions", v1, v3),
        ("a first bundle that is not version 1", None, v2),
        ("the same version again", v1, v1),
        ("another scope", v1, other_scope),
        ("another mesh", v1, other_mesh),
        ("another kind", v1, other_kind),
        ("naming the wrong predecessor", v1, wrong_parent),
    ]
    return [{"name": n, "current": None if c is None else _text(c), "candidate": _text(x),
             "errors": check_successor(c, x)} for n, c, x in rows]


_PHRASES = [
    "mass register",
    "Mass Register",
    "  MASS   register  ",
    "ＭＡＳＳ　ｒｅｇｉｓｔｅｒ",
    "ﬁle",  # the fi ligature folds under NFKC
    "Straße",  # no full Unicode case folding: ß stays
    "ÀÉÎ",  # non-ASCII capitals stay capitals
    "批量注册",
    "①②",
    "é",  # combining accent composes under NFKC
]


def build() -> dict:
    operators = Allowlist({OPERATOR.did})
    masters = MasterSet([m.did for m, _ in MASTERS], THRESHOLD)
    cases = []
    for name, obj in _cases():
        verdict = verify_bundle(obj, operators=operators, masters=masters)
        cases.append({"name": name, "bundle": _text(obj), "expect": verdict.to_json()})
    return {
        "spec": "verify_bundle(json.loads(bundle), operators={operator}, masters=MasterSet(masters, threshold))",
        "operator": {"did": OPERATOR.did, "seed_hex": OPERATOR_SEED},
        "outsider": {"did": OUTSIDER.did, "seed_hex": OUTSIDER_SEED},
        "masters": [{"did": m.did, "seed_hex": seed} for m, seed in MASTERS],
        "threshold": THRESHOLD,
        "floor": dict(FLOOR),
        "normalize": [{"input": p, "normalized": normalize_phrase(p)} for p in _PHRASES],
        "cases": cases,
        "chain": _chain(),
    }
