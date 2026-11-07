"""``admin/v1``: what the operator's App may ask the node about itself.

Read-only for now. ``status`` answers with what Studio's node and mesh view
shows:

  * who the node is and where: DID, mesh, roles, the address it listens on,
    its carriers, how many relays and rendezvous servers it was given;
  * how long it has been awake, whether it was halted, and whether the App is
    reachable;
  * its work: how many goals are pending, active, done or failed, and the
    latest few by title.

Only the node's own App allowlist may ask (the transport already drops
everyone else; this is the second door). Requests that change something
(carriers, discovery, ingest) will come with their own operator signatures.

Wire (JSON in both directions)::

    App -> node   {"op": "status", "req": "<id>"}
    node -> App   {"req": "<id>", "ok": true, "status": {...}}
                  {"req": "<id>", "ok": false, "errors": ["op:op"]}
"""
from __future__ import annotations

import json
from collections.abc import Callable

from .policy_host import ChannelClient

ADMIN_CHANNEL = "admin/v1"
RECENT_GOALS = 6


def serve(mux, ops: dict[str, Callable[[], dict]], *, may_send: Callable[[str], bool]) -> None:
    """Answer ``admin/v1`` requests on ``mux``: ``ops`` maps an op to what it returns."""

    def handle(sender: str, payload: bytes) -> None:
        if not may_send(sender):
            return
        try:
            msg = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(msg, dict) or not isinstance(msg.get("req"), str):
            return
        fn = ops.get(msg.get("op")) if isinstance(msg.get("op"), str) else None
        reply = {"ok": True, msg["op"]: fn()} if fn is not None else {"ok": False, "errors": ["op:op"]}
        reply["req"] = msg["req"][:64]
        mux.send(sender, ADMIN_CHANNEL, json.dumps(reply, ensure_ascii=False).encode("utf-8"))

    mux.channel(ADMIN_CHANNEL, handle)


def goal_counts(goals) -> dict[str, int]:
    counts = {"pending": 0, "active": 0, "done": 0, "failed": 0}
    for g in goals:
        counts[g.status] = counts.get(g.status, 0) + 1
    return counts


def recent_goals(goals, n: int = RECENT_GOALS) -> list[dict]:
    """The latest ``n`` goals, newest first."""
    return [{"id": g.id, "title": g.title, "status": g.status} for g in list(goals)[-n:]][::-1]


class AdminClient(ChannelClient):
    """An App's side of ``admin/v1``."""

    def __init__(self, mux, node_did: str):
        super().__init__(mux, node_did, ADMIN_CHANNEL)

    def status(self, *, timeout: float = 5.0) -> dict | None:
        """The node's status, or None when it did not answer in time."""
        reply = self.request("status", timeout=timeout)
        return reply.get("status") if reply is not None and reply.get("ok") else None


__all__ = ["ADMIN_CHANNEL", "AdminClient", "goal_counts", "recent_goals", "serve"]
