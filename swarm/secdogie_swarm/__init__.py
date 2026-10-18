"""secdogie-swarm: the core-side of distributed public-compute dispatch.

The resident core (Cloudflare Workers, in production) hands **public web work**
to volunteer idle browser nodes, which are both unreliable and untrusted. This
package is the core logic for that, built to be run and tested headless:

  * `tasks`       -- task/result schemas in canonical JSON, and the hard rule
                     that a node task carries public work only (no credentials);
  * `registry`    -- node liveness/health and the suspect->migrate signal;
  * `consensus`   -- verify a node's signature, decide by quorum, drop outliers;
  * `coordinator` -- assign, predictively migrate off failing nodes, collect,
                     and emit a core-signed consensus record to anchor;
  * `worker`      -- a reference node executor (reuses `agent.browse`), for the
                     simulation harness and as the contract a browser node ports.

Inference is NOT here and never runs on a node: the API key stays in the core.
"""

from __future__ import annotations

from .anchor import DagAnchor
from .consensus import Tally, quorum_for, verify_result
from .coordinator import DECIDED, FAILED, PENDING, SwarmCoordinator
from .registry import DEAD, HEALTHY, SUSPECT, HealthPolicy, NodeRegistry
from .tasks import (
    PROTOCOL,
    TASK_KINDS,
    TaskError,
    assert_public,
    build_crawl,
    build_result,
    output_digest,
    sign_task,
    validate_task,
)

__all__ = [
    "PROTOCOL",
    "TASK_KINDS",
    "TaskError",
    "assert_public",
    "build_crawl",
    "build_result",
    "output_digest",
    "sign_task",
    "validate_task",
    "NodeRegistry",
    "HealthPolicy",
    "HEALTHY",
    "SUSPECT",
    "DEAD",
    "Tally",
    "quorum_for",
    "verify_result",
    "SwarmCoordinator",
    "PENDING",
    "DECIDED",
    "FAILED",
    "DagAnchor",
]
