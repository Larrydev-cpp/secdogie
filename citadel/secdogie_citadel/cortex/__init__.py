"""The cortex: SecDogie's own decision model, a network of spiking neurons that learns and improves itself.

- ``questions``: the Socratic questions; every answer is one answer neuron.
- ``senses``: what is said becomes input spikes (no table of words).
- ``neurons``: leaky integrate-and-fire neurons, some with adaptive thresholds, and the answer neurons.
- ``plasticity``: e-prop (eligibility traces times a broadcast learning signal), neuromodulated.
- ``hippocampus``: experiences kept at once and recalled by neural similarity.
- ``familiar``: what it has met before; the unfamiliar is what it is not sure of and what it looks up.
- ``sleep``: replay and consolidation, pruning, growth, renewal.
- ``curriculum``: lessons, the external model's judgments it learns from.
- ``genome``, ``core``: one cortex and its generations.
- ``exam``, ``improver``, ``rsi``, ``life``: self-examination and recursive self-improvement.
"""
from __future__ import annotations

from .core import Cortex, Understanding
from .episode_vsa import (
    EPISODE_ROLES,
    MAX_EPISODE_SLOTS,
    CleanupCodebook,
    CleanupMatch,
    bind_episode,
    decode_episode,
    decode_role,
    unbind_role,
)
from .hippocampus import ConcreteAnchor, ConcreteHit, slot_compatibility
from .questions import QUESTIONS
from .shard import (
    EXACT,
    LOCAL_RECURRENT,
    NetShard,
    ShardedRunResult,
    ShardMesh,
    ShardNode,
    ShardVerificationError,
    pack_spikes,
    partition_net,
    unpack_spikes,
)
from .vsa_cortex import AssociativeVSACortex, System2Refiner, fuse_bimodal, hemisphere_jsd

__all__ = [
    "AssociativeVSACortex",
    "CleanupCodebook",
    "CleanupMatch",
    "ConcreteAnchor",
    "ConcreteHit",
    "Cortex",
    "EPISODE_ROLES",
    "EXACT",
    "LOCAL_RECURRENT",
    "MAX_EPISODE_SLOTS",
    "NetShard",
    "QUESTIONS",
    "ShardMesh",
    "ShardNode",
    "ShardVerificationError",
    "ShardedRunResult",
    "System2Refiner",
    "Understanding",
    "bind_episode",
    "decode_episode",
    "decode_role",
    "fuse_bimodal",
    "hemisphere_jsd",
    "pack_spikes",
    "partition_net",
    "slot_compatibility",
    "unbind_role",
    "unpack_spikes",
]
