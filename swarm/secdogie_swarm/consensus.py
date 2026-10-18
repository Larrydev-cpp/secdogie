"""Nodes are untrusted, so a result is believed only when it is (a) signed by
the node it was assigned to and (b) agreed by a quorum of independent nodes.

`verify_result` checks the signature and that the result belongs to this task
and to an assigned node. `Tally` groups verified results by their output digest
and decides once one digest reaches the quorum; results carrying any other
digest are outliers and are dropped (and their nodes earn a health penalty
upstream). A result that does not verify never counts at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from secdogie_identity import Allowlist, verify_payload

from .tasks import PROTOCOL, output_digest


def verify_result(
    result: Any,
    *,
    task_id: str,
    assigned: set[str],
    allowlist: Allowlist,
) -> tuple[bool, str | None, str | None, str | None]:
    """(ok, reason, node_did, digest).

    ok only when: the signature is valid and the signer is an enrolled node;
    the signer is exactly the claimed node_did; the result is for this task;
    and that node was actually assigned this task.
    """
    if not isinstance(result, dict):
        return (False, "not an object", None, None)
    if result.get("protocol") != PROTOCOL:
        return (False, "wrong protocol", None, None)
    valid, signer = verify_payload(result, allowlist)
    if not valid:
        return (False, "signature not valid or node not enrolled", signer, None)
    if result.get("node_did") != signer:
        return (False, "node_did does not match signer", signer, None)
    if result.get("task_id") != task_id:
        return (False, "result is for another task", signer, None)
    if signer not in assigned:
        return (False, "node was not assigned this task", signer, None)
    output = result.get("output")
    if not isinstance(output, dict):
        return (False, "missing output", signer, None)
    return (True, None, signer, output_digest(output))


@dataclass
class Tally:
    """Votes for one task, keyed by output digest."""

    votes: dict[str, set[str]] = field(default_factory=dict)

    def add(self, node_did: str, digest: str) -> None:
        self.votes.setdefault(digest, set()).add(node_did)

    def decide(self, quorum: int) -> tuple[str, frozenset[str]] | None:
        """The digest that reached `quorum` agreeing nodes, with those nodes.
        None if no digest has enough votes yet."""
        best: tuple[str, set[str]] | None = None
        for digest, nodes in self.votes.items():
            if len(nodes) >= quorum and (best is None or len(nodes) > len(best[1])):
                best = (digest, nodes)
        if best is None:
            return None
        return (best[0], frozenset(best[1]))

    def outliers(self, winning_digest: str) -> frozenset[str]:
        """Nodes whose digest disagreed with the winner."""
        out: set[str] = set()
        for digest, nodes in self.votes.items():
            if digest != winning_digest:
                out |= nodes
        return frozenset(out)


def quorum_for(redundancy: int) -> int:
    """Default quorum: a strict majority of the assigned copies."""
    return redundancy // 2 + 1
