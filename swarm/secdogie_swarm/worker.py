"""A reference node executor: run one public task and sign the result.

This is the contract a real browser node (TypeScript + WASM) ports, and what
the simulation harness uses for an honest node. It reuses the slice-1 read-only
web perception (`agent.browse`), imported lazily so the swarm core does not
hard-depend on the agent package. A node never receives a credential, so there
is nothing secret to handle here.
"""

from __future__ import annotations

from typing import Any

from secdogie_identity import Allowlist, Identity, sign_payload, verify_payload

from .tasks import build_result, validate_task


def run_crawl(
    signed_task: dict[str, Any],
    identity: Identity,
    *,
    now: float,
    opener: Any | None = None,
    core_allow: Allowlist | None = None,
) -> dict[str, Any]:
    """Execute a `crawl` task and return a node-signed result.

    `core_allow`, if given, is the set of core DIDs this node trusts to issue
    tasks -- the node refuses a task not signed by one of them.
    """
    if core_allow is not None:
        ok, _ = verify_payload(signed_task, core_allow)
        if not ok:
            raise ValueError("task not signed by a trusted core")
    task = {k: v for k, v in signed_task.items() if k not in ("signer", "sig")}
    validate_task(task)
    if task["kind"] != "crawl":
        raise ValueError(f"this worker only runs crawl, not {task['kind']!r}")

    from secdogie_agent.browse import make_policy, scan_page

    spec = task["spec"]
    policy = make_policy(spec["allowed_origins"])
    sight = scan_page(policy, spec["url"], **({} if opener is None else {"opener": opener}))
    o = sight.outcome
    doc = sight.scan["document"] if sight.scan else None
    output = {
        "kind": o.kind,
        "url": o.url,
        "status": o.status,
        "content_hash": o.content_hash,
        "document_key": (doc["key"] if doc else None),
        "candidate_keys": sorted(c["key"] for c in sight.candidates),
        "truncated": o.truncated,
    }
    result = build_result(task_id=task["task_id"], node_did=identity.did, output=output, produced_at=now)
    return sign_payload(identity, result)
