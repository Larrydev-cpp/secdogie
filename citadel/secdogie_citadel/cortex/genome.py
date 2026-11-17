"""The genome: every setting the cortex may change about itself, each within the range it may move in.

The self-improvement loop (``rsi.py``) changes genes; it cannot add one, remove one, or move one outside its
range. Genes cover four layers: the structure of the network, how it learns, how it sleeps, and how it
improves itself (so the improvement method is itself improved).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Gene:
    default: float
    low: float
    high: float
    whole: bool = False  # a count
    layer: str = "structure"  # structure, learning, sleep, apprentice, improver
    log: bool = False  # mutate on a log scale

    def clip(self, v: float) -> float:
        v = min(self.high, max(self.low, v))
        return float(round(v)) if self.whole else float(v)


GENES: dict[str, Gene] = {
    # structure
    "n_plain": Gene(1024, 32, 8192, True, log=True),
    "n_adaptive": Gene(256, 0, 4096, True, log=True),
    "p_conn": Gene(0.15, 0.02, 0.5),
    "beta": Gene(1.7, 0.0, 4.0),
    "tau_m": Gene(20.0, 5.0, 60.0, log=True),
    "tau_a": Gene(600.0, 50.0, 3000.0, log=True),
    "refractory": Gene(2, 1, 5, True),
    "kappa": Gene(0.97, 0.9, 0.995),
    "in_gain": Gene(1.5, 0.2, 6.0, log=True),
    "rec_gain": Gene(1.0, 0.0, 3.0),
    # learning
    "lr": Gene(3e-3, 1e-4, 3e-2, layer="learning", log=True),
    "reading_weight": Gene(0.3, 0.0, 2.0, layer="learning"),
    "safety_weight": Gene(2.0, 1.0, 5.0, layer="learning"),
    "rate_target": Gene(0.03, 0.005, 0.1, layer="learning", log=True),
    "rate_reg": Gene(0.5, 0.0, 20.0, layer="learning"),
    "sense_noise": Gene(0.15, 0.0, 0.5, layer="learning"),  # share of input spikes that fail to arrive while learning
    "batch": Gene(48, 8, 128, True, layer="learning", log=True),
    # sleep
    "epochs": Gene(12, 1, 60, True, layer="sleep"),
    "replay_old": Gene(0.5, 0.0, 1.0, layer="sleep"),
    "prune_below": Gene(0.002, 0.0, 0.05, layer="sleep"),
    "grow_by": Gene(64, 0, 1024, True, layer="sleep", log=True),
    "renew_share": Gene(0.02, 0.0, 0.2, layer="sleep"),
    # hippocampus and apprenticeship
    "recall_sharpness": Gene(30.0, 1.0, 200.0, layer="apprentice", log=True),
    "recall_trust": Gene(0.92, 0.5, 0.995, layer="apprentice"),
    "own_after": Gene(30, 5, 500, True, layer="apprentice", log=True),
    "spot_check": Gene(0.05, 0.01, 0.5, layer="apprentice", log=True),
    "sure_at": Gene(0.75, 0.5, 0.99, layer="apprentice"),
    "novel_weight": Gene(1.0, 0.0, 3.0, layer="apprentice"),
    # the improvement method itself
    "candidates": Gene(3, 1, 8, True, layer="improver"),
    "explore": Gene(0.25, 0.0, 1.0, layer="improver"),
    "mutation": Gene(0.2, 0.05, 0.6, layer="improver"),
    "fine_tune_epochs": Gene(4, 1, 30, True, layer="improver"),
}


def default() -> dict[str, float]:
    return {k: g.default for k, g in GENES.items()}


def clip(genome: dict) -> dict[str, float]:
    """Every gene present and inside its range; anything else is dropped."""
    return {k: g.clip(float(genome.get(k, g.default))) for k, g in GENES.items()}


def mutate(genome: dict, rng: np.random.Generator, keys: list[str] | None = None, scale: float | None = None) -> dict[str, float]:
    """Each chosen gene multiplied (or nudged) by a factor near 1: the explore step of population-based training."""
    out = dict(genome)
    s = scale if scale is not None else genome.get("mutation", GENES["mutation"].default)
    for k in keys or list(GENES):
        g = GENES[k]
        v = out[k]
        if g.log or v > 0:
            v *= float(np.exp(rng.normal(0.0, s)))
        else:
            v += float(rng.normal(0.0, s * (g.high - g.low) * 0.2))
        out[k] = g.clip(v)
    return out


def layer(name: str) -> list[str]:
    return [k for k, g in GENES.items() if g.layer == name]


__all__ = ["GENES", "Gene", "clip", "default", "layer", "mutate"]
