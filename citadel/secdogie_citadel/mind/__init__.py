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
from .plan import DISSUADE, PROCEED, REFUSE, STEPS, Thought, think, weightiest
from .world import ACTS, NOKNOWN, NOTHING, WEIGHTY, Known, Things

__all__ = ["ACTS", "ASK", "DISSUADE", "Decision", "Known", "NOKNOWN", "NOTHING", "PROCEED", "RANK", "REFUSE", "STEPS", "SUPPORT",
           "SURE_AT", "TALK", "Things", "Thought", "WEIGHTY", "decide", "think", "weightiest"]
