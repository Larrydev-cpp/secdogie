"""secdogie-policy: signed, declarative, tighten-only policy bundles.

Operators "inject" Socratic gate rules, a decision-model configuration,
ingestion limits and allowlist changes into a mesh as bundles: data with a
closed schema, never code. A bundle can only make a gate stricter than the
built-in floor; Gate 2, HITL for high-risk actions and the capability allowlist
are not in any schema. See docs/PLAN-STUDIO-INGEST.zh.md section 5.
"""
from __future__ import annotations

from .bundle import (
    BUNDLE_TYPE,
    MASTER_KINDS,
    OPERATOR_KINDS,
    Verdict,
    assemble,
    bundle_id,
    check_bundle,
    check_successor,
    create_bundle,
    sign_bundle,
    verify_bundle,
)
from .floor import FLOOR
from .schema import KINDS, MODES, POLICIES, ROLES, STAGES
from .text import contains_phrase, normalize_phrase

__version__ = "0.1.0"

__all__ = [
    "BUNDLE_TYPE",
    "FLOOR",
    "KINDS",
    "MASTER_KINDS",
    "MODES",
    "OPERATOR_KINDS",
    "POLICIES",
    "ROLES",
    "STAGES",
    "Verdict",
    "assemble",
    "bundle_id",
    "check_bundle",
    "check_successor",
    "contains_phrase",
    "create_bundle",
    "normalize_phrase",
    "sign_bundle",
    "verify_bundle",
]
