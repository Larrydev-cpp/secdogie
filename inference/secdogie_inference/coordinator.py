"""The central server: advice, repairs, and the fallback path -- never the
authority.

In the diagram the central server sits above the mesh, linked to a few serving
nodes and to the key authority; when nodes fail, everything "connects to the
central server". Here that is three jobs, none of which makes the coordinator a
trust root:

  * **Advisories.** Every ``advise_every`` seconds it signs what it sees: nodes
    whose heartbeats stopped (``failed``), nodes that enough distinct
    reporters accused of mishandling traffic (``quarantined``, via
    ``FaultTracker``), and repairs -- ``bypass`` links around the down nodes and
    ``recruit`` pairs that attach a standby forwarder to a model node left
    without a transit link. Nodes act on the repairs that name them; a link
    only exists once both ends confirm it in their own heartbeats.
  * **The central fallback.** It attaches itself to model nodes left without a
    transit link, and carries ``decider -> coordinator -> model node`` legs when
    a decider has no mesh path. It carries signed frames it cannot alter.
  * **Nothing in lockdown.** Once a mutiny latches, it carries no data at all
    -- in state 3 of the diagram the central server keeps only its link to the
    key authority. It still floods incidents and advisories.

What it cannot do: forge an answer (model nodes sign them), latch or clear a
mutiny (that takes evidence, and an operator), or un-quarantine a node early
for anyone but itself -- a node's own ``Suspicion`` and the ledger still apply.
"""
from __future__ import annotations

import time

from .health import FaultTracker
from .participant import Carrier, Config
from .protocol import ROLE_COORDINATOR, ROLE_SERVE, check_fault, make_advisory
from .topology import plan_repairs


class Coordinator(Carrier):
    roles = frozenset({ROLE_COORDINATOR})

    def __init__(self, identity, wire, trust, *, standby=(), config: Config | None = None, clock=time.time):
        super().__init__(identity, wire, trust, config=config, clock=clock)
        if not trust.coordinators.contains(self.did):
            raise ValueError("a coordinator must be on the coordinators list")
        cfg = self.config
        self.standby = list(standby)
        self.faults = FaultTracker(threshold=cfg.quarantine_threshold, window=cfg.quarantine_window,
                                   cooldown=cfg.quarantine_cooldown, probation=cfg.probation)
        self._epoch = 0
        self._next_advice = 0.0
        self._dispatch["fault"] = self._on_fault

    def _on_fault(self, src: str, f: dict) -> None:
        now = float(self.clock())
        report = f.get("report")
        if check_fault(report, self.trust, now=now) is not None or report["reporter"] != src:
            return
        if self.faults.report(report["accused"], src, now):
            self.note("quarantine", node=report["accused"][-8:], reason=report["reason"])

    def may_carry(self, decision: dict, now: float) -> str | None:
        if self.ledger.lockdown:
            return "lockdown: the central server is off the data path"
        return super().may_carry(decision, now)

    def on_tick(self, now: float) -> None:
        if now >= self._next_advice:
            self._next_advice = now + self.config.advise_every
            self.advise(now)

    def advise(self, now: float) -> dict:
        """Sign and flood an advisory for what this coordinator sees now."""
        known = [d for d in self.lsdb.known() if d != self.did]
        failed = sorted(d for d in known if not self.lsdb.alive(d, now))
        quarantined = sorted(self.faults.quarantined(now))
        down = set(failed) | set(quarantined) | {d for d in known if self.ledger.excludes_node(d)}
        deciders = [d for d in known if ROLE_SERVE in self.lsdb.roles(d)]
        bypass, recruit, orphans = plan_repairs(self.lsdb, now, down=down, standby=self.standby,
                                                deciders=deciders)
        for provider in orphans:
            if provider not in self.neighbors and self.connect(provider):
                self.note("central-attach", node=provider[-8:])
        self._epoch = max(self._epoch + 1, int(now * 1000))
        advisory = make_advisory(self.identity, epoch=self._epoch, failed=failed, quarantined=quarantined,
                                 bypass=bypass, recruit=recruit, clock=self.clock)
        if failed or quarantined or bypass or recruit:
            self.note("advise", epoch=self._epoch, failed=[d[-8:] for d in failed],
                      quarantined=[d[-8:] for d in quarantined], bypass=len(bypass), recruit=len(recruit))
        self.publish(advisory)
        return advisory


__all__ = ["Coordinator"]
