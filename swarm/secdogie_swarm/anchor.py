"""Write a decided consensus into the real topology DAG.

Once a crawl's result is agreed by quorum, the core turns its public output into
one signed, append-only delta and ingests it into the `graph` DAG (via the
`secdogie_graph.DagStore`). The delta is authored and signed by the core, so the
DAG only ever grows from the core's own verified consensus -- a node's raw,
unverified claim never reaches it.

The store is injected (duck-typed: `heads()` and `ingest(wire)`), so this module
has no hard dependency on the native binding; only a caller that actually
anchors needs it built.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from secdogie_identity import Identity, sign_payload

DELTA_TYPE = "secdogie/state-graph-delta/v1"


class DagLike(Protocol):
    def heads(self) -> dict[str, Any]: ...

    def ingest(self, wire: str) -> dict[str, Any]: ...


class DagAnchor:
    def __init__(self, core_identity: Identity, store: DagLike) -> None:
        self.core = core_identity
        self.store = store

    def anchor_crawl(self, output: dict[str, Any]) -> dict[str, Any] | None:
        """Ingest one crawl consensus as a signed delta. Returns the ingest
        outcome, or None when there is nothing to anchor (not a page)."""
        doc = output.get("document")
        if not doc:
            return None
        ops: list[dict[str, Any]] = [
            {"op": "add_state", "origin": doc["origin"], "route": doc["route"], "query_keys": list(doc["query_keys"])}
        ]
        if output.get("content_hash") and output.get("byte_len") is not None:
            ops.append(
                {
                    "op": "add_observation",
                    "state": doc["key"],
                    "content_hash": output["content_hash"],
                    "byte_len": int(output["byte_len"]),
                }
            )
        for c in output.get("candidates", []):
            ops.append(
                {"op": "add_state", "origin": c["origin"], "route": c["route"], "query_keys": list(c["query_keys"])}
            )
            ops.append(
                {
                    "op": "add_reference",
                    "from": doc["key"],
                    "to": c["key"],
                    "via": c["via"],
                    "method": c["method"],
                    "fields": list(c["fields"]),
                }
            )
        h = self.store.heads()
        payload = {
            "type": DELTA_TYPE,
            "author": self.core.did,
            "parents": sorted(h["heads"]),
            "lamport": h["next_lamport"],
            "ops": ops,
        }
        env = sign_payload(self.core, payload)
        return self.store.ingest(json.dumps(env))
