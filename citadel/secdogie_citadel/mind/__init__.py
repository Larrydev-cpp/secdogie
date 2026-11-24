"""The thinking module: what the companion does with what the cortex understood.

The cortex (``cortex/``) reads what the person said and answers the Socratic questions, each answer with
how sure it is. This package thinks with those answers; it reads no words itself.

- ``world``: the request as a small world: the kind of change it makes, the facts of the moment, the rules of
  what follows from what.
- ``belief``: what is not known (will they be sorry later, is there another copy, can the other side be
  trusted, what are they after), each with a probability from the facts.
- ``plan``: the ways forward are generated, each is played out in every way the unknowns could turn out,
  and the best is chosen.
- ``decide``: danger first, then talk, the principle, one question when only the person can settle it, and
  thinking it through. Going ahead is a conclusion, never a default.
- ``history``: what the companion has learned about this person from their own record.
"""
from __future__ import annotations

from .decide import ASK, RANK, SUPPORT, SURE_AT, TALK, Decision, decide
from .dream import (
    DEFAULT_MAX_CHAIN,
    DEFAULT_MIN_NOVELTY,
    DEFAULT_TOP_K,
    CausalBrick,
    DreamReport,
    DreamSpark,
    ECNVerdict,
    MacroSkill,
    TargetDomain,
    analogical_transfer,
    compositional_replay,
    deconstruct_anchor,
    deconstruct_hippocampus,
    dream_cycle,
    promote_macro_skill,
    score_salience,
    verify_spark,
)
from .plan import DISSUADE, PROCEED, REFUSE, STEPS, Thought, think, weightiest
from .trajectory import (
    EFFECT_ATOMS,
    TRAJECTORY_RULES,
    CompositionalRule,
    TrajectoryAssessment,
    TrajectoryStep,
    TriggeredChain,
    WorldTrajectory,
    infer_effects,
)
from .world import ACTS, NOKNOWN, NOTHING, WEIGHTY, Known, Things

__all__ = [
    "ACTS",
    "ASK",
    "CausalBrick",
    "CompositionalRule",
    "DEFAULT_MAX_CHAIN",
    "DEFAULT_MIN_NOVELTY",
    "DEFAULT_TOP_K",
    "DISSUADE",
    "Decision",
    "DreamReport",
    "DreamSpark",
    "ECNVerdict",
    "EFFECT_ATOMS",
    "Known",
    "MacroSkill",
    "NOKNOWN",
    "NOTHING",
    "PROCEED",
    "RANK",
    "REFUSE",
    "STEPS",
    "SUPPORT",
    "SURE_AT",
    "TALK",
    "TRAJECTORY_RULES",
    "TargetDomain",
    "Things",
    "Thought",
    "TrajectoryAssessment",
    "TrajectoryStep",
    "TriggeredChain",
    "WEIGHTY",
    "WorldTrajectory",
    "analogical_transfer",
    "compositional_replay",
    "decide",
    "deconstruct_anchor",
    "deconstruct_hippocampus",
    "dream_cycle",
    "infer_effects",
    "promote_macro_skill",
    "score_salience",
    "think",
    "verify_spark",
    "weightiest",
]
