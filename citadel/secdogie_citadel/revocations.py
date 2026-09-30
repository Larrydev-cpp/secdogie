"""Revocations carried in the journal (T6), so they last and reach every node.

A revocation record (``secdogie_identity.revocation``) is self-authenticating:
its authority is the k-of-n master signatures it carries, not whoever passes it
on. The fast path floods it to the nodes that are online
(``transport.revocation_gossip``); this is the durable path: a node writes the
record into its journal as a ``revocation`` event, replication carries it, and
a node that was offline catches up when it rejoins.

Nothing here verifies or applies a record -- that needs the master set, which
the node holds (``TrustPolicy.apply``). The journal's own checks only prove who
wrote the event down; a forged record rides along harmlessly and is refused
where it is applied.
"""
from __future__ import annotations

from typing import Any

REVOCATION_KIND = "revocation"


def publish_revocation(journal: Any, record: dict) -> dict:
    """Write ``record`` (as signed by the masters, unchanged) into the journal."""
    return journal.append(REVOCATION_KIND, record)


def revocation_events(events) -> list[dict]:
    """The journal's revocation events whose body is a record-shaped object, in
    journal order. Unverified: the caller checks each against its masters."""
    return [e for e in events if e.get("kind") == REVOCATION_KIND and isinstance(e.get("body"), dict)]


__all__ = ["REVOCATION_KIND", "publish_revocation", "revocation_events"]
