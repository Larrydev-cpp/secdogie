"""Closed schemas for policy bundles, and the tighten-only check.

``check_body(kind, body)`` and the envelope check in bundle.py return a list of
errors, each ``"<code>:<path>"``, de-duplicated and sorted by code point. The
TypeScript validator (symbiont/src/policy/bundle.ts) produces the same list for
the same input; the golden vectors in fixtures/vectors/policy.json hold the two
together, so the rules below are written to be mirrored exactly.

Codes:

  type           wrong JSON type (an int field given a float or a bool counts)
  missing        a required field is absent
  unknown-field  a field the schema does not name (schemas are closed)
  range          a number or a length outside what the schema allows
  loosens        a number looser than the built-in floor (floor.py)
  format         a string that does not match its pattern (DID, origin, hex,
                 token, mesh name), or that contains a control character
  not-normalized a phrase that is not in normal form (text.py)
  order          a list of strings that is not sorted and duplicate-free
  value          a string outside its enumeration, or a wrong constant
  conflict       fields that cannot go together

Paths are dotted keys from the bundle root, ``[i]`` for list items, ``$`` for
the root itself. Descent stops at a value of the wrong type, so one mistake is
one error. Boolean "switch it on" fields accept only ``true``: ``false`` is
``loosens``, because a bundle can only make a gate stricter.
"""
from __future__ import annotations

import re

from secdogie_identity import pubkey_from_did

from .floor import FLOOR
from .text import has_control, normalize_phrase

KIND_GATE_RULES = "gate-rules"
KIND_DECISION_MODEL = "decision-model"
KIND_INGEST = "ingest"
KIND_ALLOWLIST = "allowlist"
KINDS = (KIND_ALLOWLIST, KIND_DECISION_MODEL, KIND_GATE_RULES, KIND_INGEST)

ROLES = ("broker", "coordinator", "desk", "forward", "ingest", "model", "relay", "serve")
POLICIES = ("llm/v1", "router/v1", "weighted/v1")
STAGES = ("canary", "full", "shadow")
MODES = ("desktop-ax", "rendered", "session", "static")

_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,31}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_MESH = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_ORIGIN = re.compile(rf"https://({_LABEL}(?:\.{_LABEL})+)(?::([1-9][0-9]{{0,4}}))?")


class Errors:
    def __init__(self):
        self._set: set[str] = set()

    def add(self, code: str, path: str) -> None:
        self._set.add(f"{code}:{path or '$'}")

    def sorted(self) -> list[str]:
        return sorted(self._set)


def join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def obj(v, path: str, errs: Errors) -> dict | None:
    if not isinstance(v, dict):
        errs.add("type", path)
        return None
    return v


def fields(d: dict, path: str, allowed, required, errs: Errors) -> None:
    for k in d:
        if k not in allowed:
            errs.add("unknown-field", join(path, k))
    for k in required:
        if k not in d:
            errs.add("missing", join(path, k))


def int_in(v, path: str, lo: int, hi: int, errs: Errors, *, floor: bool = False) -> bool:
    """``hi`` is the floor when ``floor`` is set: above it is ``loosens``."""
    if not is_int(v):
        errs.add("type", path)
        return False
    if v < lo:
        errs.add("range", path)
        return False
    if v > hi:
        errs.add("loosens" if floor else "range", path)
        return False
    return True


def true_only(v, path: str, errs: Errors) -> None:
    if not isinstance(v, bool):
        errs.add("type", path)
    elif v is False:
        errs.add("loosens", path)


def text(v, path: str, lo: int, hi: int, errs: Errors) -> bool:
    if not isinstance(v, str):
        errs.add("type", path)
        return False
    if not lo <= len(v) <= hi:
        errs.add("range", path)
        return False
    if has_control(v):
        errs.add("format", path)
        return False
    return True


def enum(v, path: str, values, errs: Errors) -> bool:
    if not isinstance(v, str):
        errs.add("type", path)
        return False
    if v not in values:
        errs.add("value", path)
        return False
    return True


def pattern(v, path: str, rx: re.Pattern, errs: Errors) -> bool:
    if not isinstance(v, str):
        errs.add("type", path)
        return False
    if rx.fullmatch(v) is None:
        errs.add("format", path)
        return False
    return True


def did(v, path: str, errs: Errors) -> bool:
    if not isinstance(v, str):
        errs.add("type", path)
        return False
    try:
        pubkey_from_did(v)
    except ValueError:
        errs.add("format", path)
        return False
    return True


def origin(v, path: str, errs: Errors) -> bool:
    if not isinstance(v, str):
        errs.add("type", path)
        return False
    m = _ORIGIN.fullmatch(v)
    ok = m is not None and len(m.group(1)) <= 253
    if ok and m.group(2) is not None:
        port = int(m.group(2))
        ok = port <= 65535 and port != 443
    if not ok:
        errs.add("format", path)
    return ok


def phrase(v, path: str, errs: Errors) -> bool:
    if not text(v, path, 1, 64, errs):
        return False
    if normalize_phrase(v) != v or v == "":
        errs.add("not-normalized", path)
        return False
    return True


def str_list(v, path: str, item, errs: Errors, *, lo: int = 0, hi: int) -> list | None:
    """A list of strings, each checked by ``item(value, path, errs)``, sorted by
    code point and duplicate-free. Order is only judged when every item passed."""
    if not isinstance(v, list):
        errs.add("type", path)
        return None
    if not lo <= len(v) <= hi:
        errs.add("range", path)
        return None
    ok = True
    for i, x in enumerate(v):
        ok = item(x, f"{path}[{i}]", errs) and ok
    if ok and v != sorted(set(v)):
        errs.add("order", path)
        return None
    return v if ok else None


def obj_list(v, path: str, item, errs: Errors, *, hi: int) -> None:
    if not isinstance(v, list):
        errs.add("type", path)
        return
    if len(v) > hi:
        errs.add("range", path)
        return
    for i, x in enumerate(v):
        item(x, f"{path}[{i}]", errs)


# ---- gate-rules ------------------------------------------------------------------


def _revise_on(v, path: str, errs: Errors) -> None:
    d = obj(v, path, errs)
    if d is None:
        return
    fields(d, path, {"phrases", "reason", "suggestion"}, {"phrases", "reason"}, errs)
    if "phrases" in d:
        str_list(d["phrases"], join(path, "phrases"), phrase, errs, lo=1, hi=32)
    if "reason" in d:
        text(d["reason"], join(path, "reason"), 1, 200, errs)
    if "suggestion" in d:
        text(d["suggestion"], join(path, "suggestion"), 0, 200, errs)


def _blocked_target(v, path: str, errs: Errors) -> None:
    d = obj(v, path, errs)
    if d is None:
        return
    fields(d, path, {"role", "name"}, (), errs)
    if "role" not in d and "name" not in d:
        errs.add("missing", join(path, "role"))
    for k in ("role", "name"):
        if k in d:
            phrase(d[k], join(path, k), errs)


def _gate_rules(body: dict, path: str, errs: Errors) -> None:
    fields(body, path, {"instruction", "plan"}, (), errs)
    if "instruction" in body:
        p = join(path, "instruction")
        d = obj(body["instruction"], p, errs)
        if d is not None:
            fields(d, p, {"max_len", "max_steps", "revise_on"}, (), errs)
            if "max_len" in d:
                int_in(d["max_len"], join(p, "max_len"), 1, FLOOR["instruction.max_len"], errs, floor=True)
            if "max_steps" in d:
                int_in(d["max_steps"], join(p, "max_steps"), 1, FLOOR["instruction.max_steps"], errs, floor=True)
            if "revise_on" in d:
                obj_list(d["revise_on"], join(p, "revise_on"), _revise_on, errs, hi=64)
    if "plan" in body:
        p = join(path, "plan")
        d = obj(body["plan"], p, errs)
        if d is not None:
            fields(d, p, {"require_intent", "enforce_capabilities", "repeat_threshold", "poll_window",
                          "known_failure_min_runs", "max_cost", "extra_destructive_kinds", "blocked_kinds",
                          "blocked_targets"}, (), errs)
            for k in ("require_intent", "enforce_capabilities"):
                if k in d:
                    true_only(d[k], join(p, k), errs)
            if "repeat_threshold" in d:
                int_in(d["repeat_threshold"], join(p, "repeat_threshold"), 1,
                       FLOOR["plan.repeat_threshold"], errs, floor=True)
            if "poll_window" in d:
                int_in(d["poll_window"], join(p, "poll_window"), 2, FLOOR["plan.poll_window"], errs, floor=True)
            if "known_failure_min_runs" in d:
                int_in(d["known_failure_min_runs"], join(p, "known_failure_min_runs"), 1,
                       FLOOR["plan.known_failure_min_runs"], errs, floor=True)
            if "max_cost" in d:
                int_in(d["max_cost"], join(p, "max_cost"), 1, 10_000, errs)
            for k in ("extra_destructive_kinds", "blocked_kinds"):
                if k in d:
                    str_list(d[k], join(p, k), lambda x, q, e: pattern(x, q, _TOKEN, e), errs, hi=32)
            if "blocked_targets" in d:
                obj_list(d["blocked_targets"], join(p, "blocked_targets"), _blocked_target, errs, hi=64)


# ---- decision-model --------------------------------------------------------------


def _decision_model(body: dict, path: str, errs: Errors) -> None:
    fields(body, path, {"policy", "weights", "deny_providers", "llm", "rollout"}, {"policy"}, errs)
    policy = body.get("policy")
    known = "policy" in body and enum(policy, join(path, "policy"), POLICIES, errs)
    if "weights" in body:
        p = join(path, "weights")
        d = obj(body["weights"], p, errs)
        if d is not None:
            fields(d, p, {"hop_milli", "load_milli"}, (), errs)
            for k in ("hop_milli", "load_milli"):
                if k in d:
                    int_in(d[k], join(p, k), 0, 10_000, errs)
    if "deny_providers" in body:
        str_list(body["deny_providers"], join(path, "deny_providers"), did, errs, hi=64)
    if "llm" in body:
        p = join(path, "llm")
        d = obj(body["llm"], p, errs)
        if d is not None:
            keys = ("max_tokens", "model", "prompt_template_hash", "timeout_ms")
            fields(d, p, keys, keys, errs)
            if "model" in d:
                text(d["model"], join(p, "model"), 1, 128, errs)
            if "prompt_template_hash" in d:
                pattern(d["prompt_template_hash"], join(p, "prompt_template_hash"), _HEX64, errs)
            if "timeout_ms" in d:
                int_in(d["timeout_ms"], join(p, "timeout_ms"), 100, 10_000, errs)
            if "max_tokens" in d:
                int_in(d["max_tokens"], join(p, "max_tokens"), 1, 4096, errs)
    stage = None
    if "rollout" in body:
        p = join(path, "rollout")
        d = obj(body["rollout"], p, errs)
        if d is not None:
            fields(d, p, {"stage", "canary", "promote_after"}, {"stage"}, errs)
            if "stage" in d and enum(d["stage"], join(p, "stage"), STAGES, errs):
                stage = d["stage"]
            if "canary" in d:
                str_list(d["canary"], join(p, "canary"), did, errs, lo=1, hi=8)
            if "promote_after" in d:
                int_in(d["promote_after"], join(p, "promote_after"), 100, 1_000_000, errs)
            if stage is not None:
                if stage == "canary" and "canary" not in d:
                    errs.add("missing", join(p, "canary"))
                if stage != "canary" and "canary" in d:
                    errs.add("conflict", join(p, "canary"))
                if stage != "shadow" and "promote_after" in d:
                    errs.add("conflict", join(p, "promote_after"))
    if known:
        builtin = policy == "router/v1"
        if builtin and "weights" in body:
            errs.add("conflict", join(path, "weights"))
        if policy == "llm/v1" and "llm" not in body:
            errs.add("missing", join(path, "llm"))
        if policy != "llm/v1" and "llm" in body:
            errs.add("conflict", join(path, "llm"))
        if not builtin and "rollout" not in body:
            errs.add("missing", join(path, "rollout"))
        if builtin and "rollout" in body:
            errs.add("conflict", join(path, "rollout"))


# ---- ingest ----------------------------------------------------------------------


def _ingest(body: dict, path: str, errs: Errors) -> None:
    fields(body, path, {"origins", "submitters", "rate", "modes", "max_body_bytes", "timeout_ms"},
           {"origins", "submitters"}, errs)
    if "origins" in body:
        str_list(body["origins"], join(path, "origins"), origin, errs, hi=256)
    if "submitters" in body:
        str_list(body["submitters"], join(path, "submitters"), did, errs, lo=1, hi=64)
    if "rate" in body:
        p = join(path, "rate")
        d = obj(body["rate"], p, errs)
        if d is not None:
            fields(d, p, {"rps_milli", "concurrency"}, (), errs)
            if "rps_milli" in d:
                int_in(d["rps_milli"], join(p, "rps_milli"), 1, FLOOR["ingest.rps_milli"], errs, floor=True)
            if "concurrency" in d:
                int_in(d["concurrency"], join(p, "concurrency"), 1, FLOOR["ingest.concurrency"], errs, floor=True)
    if "modes" in body:
        str_list(body["modes"], join(path, "modes"), lambda x, q, e: enum(x, q, MODES, e), errs, lo=1, hi=4)
    if "max_body_bytes" in body:
        int_in(body["max_body_bytes"], join(path, "max_body_bytes"), 1, FLOOR["ingest.max_body_bytes"], errs,
               floor=True)
    if "timeout_ms" in body:
        int_in(body["timeout_ms"], join(path, "timeout_ms"), 1000, FLOOR["ingest.timeout_ms"], errs, floor=True)


# ---- allowlist -------------------------------------------------------------------


def _allowlist(body: dict, path: str, errs: Errors) -> None:
    fields(body, path, {"add", "remove"}, (), errs)
    add = str_list(body["add"], join(path, "add"), did, errs, hi=256) if "add" in body else []
    remove = str_list(body["remove"], join(path, "remove"), did, errs, hi=256) if "remove" in body else []
    if add is not None and remove is not None:
        if not add and not remove:
            errs.add("conflict", path)  # changes nothing
        elif set(add) & set(remove):
            errs.add("conflict", join(path, "remove"))


_BODY = {
    KIND_GATE_RULES: _gate_rules,
    KIND_DECISION_MODEL: _decision_model,
    KIND_INGEST: _ingest,
    KIND_ALLOWLIST: _allowlist,
}


def check_body(kind: str, body, errs: Errors, path: str = "body") -> None:
    d = obj(body, path, errs)
    if d is not None:
        _BODY[kind](d, path, errs)


def check_scope(v, path: str, errs: Errors) -> None:
    d = obj(v, path, errs)
    if d is None:
        return
    fields(d, path, ("nodes", "roles"), ("nodes", "roles"), errs)
    if "roles" in d:
        str_list(d["roles"], join(path, "roles"), lambda x, q, e: enum(x, q, ROLES, e), errs, hi=len(ROLES))
    if "nodes" in d:
        str_list(d["nodes"], join(path, "nodes"), did, errs, hi=256)


def mesh_name(v, path: str, errs: Errors) -> bool:
    return pattern(v, path, _MESH, errs)


def hex64(v, path: str, errs: Errors) -> bool:
    return pattern(v, path, _HEX64, errs)
