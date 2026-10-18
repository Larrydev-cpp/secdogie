"""The unit of work the core hands to a volunteer node, and the result a node
signs back -- both canonical JSON (`secdogie_identity.canonical`), so a
signature is reproducible byte-for-byte.

The one invariant that matters most here: a task dispatched to a volunteer node
carries **public work only**. No API key, no login credential, no private
session context ever goes into a task. That is enforced two ways:

  * structurally -- a task of a given kind has a fixed, allowlisted field set,
    and the builder never copies arbitrary core state into it;
  * defensively -- `assert_public` walks the whole task (minus its signature
    envelope) and refuses any secret-shaped key name.

Inference does not happen on nodes, so no prompt/model/credential material is
part of any task kind. Nodes only fetch public pages and compress them.
"""

from __future__ import annotations

import hashlib
from typing import Any

from secdogie_identity import Identity, canonical

PROTOCOL = "secdogie/swarm/v1"

# Public web work only (slice 0). "crawl" = bounded public fetch + route scan.
TASK_KINDS = frozenset({"crawl"})

# The fixed public field set per kind. A task's spec must match exactly -- no
# extra keys, so core state can never ride along.
SPEC_FIELDS: dict[str, frozenset[str]] = {
    "crawl": frozenset({"url", "allowed_origins"}),
}

# Signature-envelope keys (public: a detached signature is not a secret).
_ENVELOPE = ("signer", "sig")

# Defense-in-depth: substrings that must never appear in a task key name.
_SECRET_HINTS = (
    "key",
    "token",
    "secret",
    "cookie",
    "auth",
    "password",
    "credential",
    "session",
    "bearer",
    "private",
    "apikey",
    "api_key",
)


class TaskError(ValueError):
    """A malformed task, or one that would carry something it must not."""


def _secret_shaped(name: str) -> bool:
    low = name.lower()
    # "key" as a whole word (the envelope "sig"/"signer" are allowed separately);
    # the hints catch api_key, auth_token, cookie, session_id, etc.
    return any(h in low for h in _SECRET_HINTS)


def assert_public(task: dict[str, Any]) -> None:
    """Raise unless `task` carries public work only. Walks every nested key,
    skipping only the top-level signature envelope."""

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    raise TaskError(f"non-string key at {path}")
                if _secret_shaped(k):
                    raise TaskError(f"secret-shaped key {k!r} at {path} -- tasks carry public work only")
                walk(v, f"{path}.{k}")
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    body = {k: v for k, v in task.items() if k not in _ENVELOPE}
    walk(body, "task")


def build_crawl(
    *,
    task_id: str,
    url: str,
    allowed_origins: list[str],
    issued_at_ms: int,
    deadline_ms: int,
    redundancy: int,
    epoch: int,
) -> dict[str, Any]:
    """A crawl task: fetch one public URL within the allowlist and report the
    candidate states it names. Only public fields; `assert_public` enforces it.
    All timestamps are integer milliseconds -- no float ever crosses the wire,
    so the canonical bytes are identical in Python, TS and Workers."""
    if redundancy < 1:
        raise TaskError("redundancy must be >= 1")
    task = {
        "protocol": PROTOCOL,
        "task_id": task_id,
        "kind": "crawl",
        "spec": {"url": url, "allowed_origins": list(allowed_origins)},
        "issued_at_ms": issued_at_ms,
        "deadline_ms": deadline_ms,
        "redundancy": redundancy,
        "epoch": epoch,
    }
    validate_task(task)
    assert_public(task)
    return task


def _int(task: dict[str, Any], field: str) -> None:
    v = task.get(field)
    # bool is an int subclass; a float on the wire is exactly what we forbid.
    if not isinstance(v, int) or isinstance(v, bool):
        raise TaskError(f"{field} must be an integer (milliseconds / count), not a float")


def validate_task(task: dict[str, Any]) -> None:
    """Structural check: known kind, exact spec field set, integer timestamps."""
    if not isinstance(task, dict):
        raise TaskError("task must be an object")
    if task.get("protocol") != PROTOCOL:
        raise TaskError("wrong or missing protocol")
    kind = task.get("kind")
    if kind not in TASK_KINDS:
        raise TaskError(f"unknown task kind {kind!r}")
    for field in ("task_id", "issued_at_ms", "deadline_ms", "redundancy", "epoch"):
        if field not in task:
            raise TaskError(f"missing {field}")
    if not isinstance(task.get("task_id"), str) or not task["task_id"]:
        raise TaskError("task_id must be a non-empty string")
    for field in ("issued_at_ms", "deadline_ms", "redundancy", "epoch"):
        _int(task, field)
    spec = task.get("spec")
    if not isinstance(spec, dict) or frozenset(spec) != SPEC_FIELDS[kind]:
        raise TaskError(f"spec for {kind} must be exactly {sorted(SPEC_FIELDS[kind])}")


def sign_task(identity: Identity, task: dict[str, Any]) -> dict[str, Any]:
    """Core-sign a task so a node (and a later auditor) can trust its origin."""
    from secdogie_identity import sign_payload

    validate_task(task)
    assert_public(task)
    return sign_payload(identity, task)


def build_result(
    *,
    task_id: str,
    node_did: str,
    output: dict[str, Any],
    produced_at_ms: int,
) -> dict[str, Any]:
    """A node's result for a task (unsigned; the node signs with `sign_payload`).
    `produced_at_ms` is integer milliseconds -- no float on the wire."""
    return {
        "protocol": PROTOCOL,
        "task_id": task_id,
        "node_did": node_did,
        "output": output,
        "produced_at_ms": produced_at_ms,
    }


def output_digest(output: dict[str, Any]) -> str:
    """Canonical hash of a result's output -- the value consensus agrees on."""
    return hashlib.sha256(canonical(output)).hexdigest()
