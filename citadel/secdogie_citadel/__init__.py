"""secdogie-citadel: the Citadel state substrate.

A signed, append-only event journal (per-author hash chains, DID signatures,
deterministic total order, idempotent merge) and a DAG goal tree projected from
it. Persistent, tamper-evident, and convergent across a node's own authorized
peers -- the durable state layer the supervised Citadel runs on. One dependency
(secdogie-identity); no network here (replication rides the fleet transport).
"""
from __future__ import annotations

from . import socratic, state, sync
from .goals import Goal, GoalTree, build_goal_tree
from .journal import GENESIS, Journal
from .socratic import Review, review
from .state import StateDelta, StateStore, record_state
from .supervisor import Supervisor, agent_run_task, terminal_confirm

__version__ = "0.5.0"

__all__ = [
    "Journal", "GENESIS", "GoalTree", "Goal", "build_goal_tree",
    "sync", "socratic", "review", "Review",
    "Supervisor", "agent_run_task", "terminal_confirm",
    "state", "StateStore", "StateDelta", "record_state",
]
