"""Candidate memory (S2): the local quarantine.

Anything that might become long-term memory waits here first: cautions distilled
from episodes ("this action failed or did nothing, in these runs") and notes the
model asked to remember (M7), which may quote text off another application's
screen. Candidates are **untrusted**:

  * local to this node -- a node's unverified notes are never replicated;
  * never rendered into a prompt and never read by a gate;
  * a work queue, not evidence: when consolidation (S3) decides whether to
    promote a caution it re-derives the evidence from verified episodes
    (``tally``), so a candidate planted in this file gains nothing.

Every entry point refuses credential-shaped text (``looks_like_secret``, the same
net as ``secdogie_agent.memory``; a test keeps the two in sync), bounds sizes,
and validates the scope. Stale candidates expire (TTL on ``last_seen``); the
store is capped, evicting the least recently seen.

SQLite, one small table; thread-safe.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum

from secdogie_identity.signing import canonical

from .episodes import Episode


class MemoryClass(str, Enum):
    CAUTION = "caution"  # can only make the gates stricter
    FACT = "fact"  # can steer planning: needs operator confirmation to promote
    PREFERENCE = "preference"  # likewise


SOURCES = ("consolidation", "model", "operator")
_SCOPE = re.compile(r"^(global|app:\S{1,200}|goal:\S{1,200})$")
MAX_KEY = 200
MAX_VALUE = 500
DEFAULT_TTL = 30 * 86400.0
DEFAULT_MAX_ITEMS = 10_000

# What counts, for a caution, as the action failing or succeeding. A gate
# rejection ("rejected") is neither: the action never ran, and counting it would
# let a caution feed itself.
FAILURE_OUTCOMES = frozenset({"failed", "no_change"})
SUCCESS_OUTCOMES = frozenset({"ok"})


class SecretRefused(ValueError):
    """Credential-shaped text never enters memory, at any stage."""


# Kept in sync with secdogie_agent.memory (tests/test_lessons.py checks it). The
# agent does not depend on citadel, so the net is repeated rather than imported.
_SECRET_KEY_HINTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "api-key",
    "pin", "cvv", "ssn", "credential", "private_key",
)
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def looks_like_secret(key: str | None, value: str) -> bool:
    k = (key or "").lower()
    if any(hint in k for hint in _SECRET_KEY_HINTS):
        return True
    return bool(_SECRET_VALUE_RE.search(value))


def candidate_id(mclass: MemoryClass, scope: str, key: str, value: str) -> str:
    body = {"mclass": MemoryClass(mclass).value, "scope": scope, "key": key, "value": value}
    return hashlib.sha256(canonical(body)).hexdigest()


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    mclass: MemoryClass
    scope: str
    key: str  # CAUTION: the action's effect hash; FACT / PREFERENCE: a stable name
    value: str
    source: str
    evidence: tuple[str, ...]  # run ids supporting it (as last seen; not trusted for promotion)
    contradictions: tuple[str, ...]  # run ids against it
    first_seen: float
    last_seen: float


def validate(mclass, scope: str, key: str, value: str, source: str) -> MemoryClass:
    """Check one would-be memory; raise ``ValueError`` (``SecretRefused`` for
    credentials) or return its class. Shared with consolidation (S3)."""
    try:
        mclass = MemoryClass(mclass)
    except ValueError:
        raise ValueError(f"unknown memory class {mclass!r}") from None
    if not isinstance(scope, str) or not _SCOPE.match(scope):
        raise ValueError(f"bad scope {scope!r} (global | app:<id> | goal:<id>)")
    if not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY:
        raise ValueError(f"key must be 1..{MAX_KEY} characters")
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_VALUE:
        raise ValueError(f"value must be 1..{MAX_VALUE} characters")
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    if looks_like_secret(key, value):
        raise SecretRefused("that looks like a credential; memory never stores secrets")
    return mclass


def make_candidate(mclass, scope: str, key: str, value: str, *, source: str, evidence=(),
                   contradictions=(), now: float) -> Candidate:
    mclass = validate(mclass, scope, key, value, source)
    return Candidate(candidate_id(mclass, scope, key, value), mclass, scope, key, value, source,
                     tuple(sorted(set(evidence))), tuple(sorted(set(contradictions))), float(now), float(now))


class CandidateStore:
    """The S2 quarantine: a local SQLite table of candidates."""

    def __init__(self, path: str = ":memory:", *, clock=time.time, ttl: float = DEFAULT_TTL,
                 max_items: int = DEFAULT_MAX_ITEMS):
        self._clock = clock
        self._ttl = float(ttl)
        self._max = int(max_items)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS candidates("
            " candidate_id TEXT PRIMARY KEY, mclass TEXT NOT NULL, scope TEXT NOT NULL,"
            " key TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL,"
            " evidence TEXT NOT NULL, contradictions TEXT NOT NULL,"
            " first_seen REAL NOT NULL, last_seen REAL NOT NULL)"
        )
        self._db.commit()

    def upsert(self, c: Candidate) -> Candidate:
        """Add ``c``, or merge it into the stored candidate with the same id
        (evidence and contradictions united, ``last_seen`` advanced). Returns
        what is stored."""
        validate(c.mclass, c.scope, c.key, c.value, c.source)
        with self._lock:
            old = self._get(c.candidate_id)
            if old is not None:
                c = Candidate(
                    old.candidate_id, old.mclass, old.scope, old.key, old.value, old.source,
                    tuple(sorted(set(old.evidence) | set(c.evidence))),
                    tuple(sorted(set(old.contradictions) | set(c.contradictions))),
                    min(old.first_seen, c.first_seen), max(old.last_seen, c.last_seen),
                )
            else:
                self._make_room()
            self._db.execute(
                "INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?)",
                (c.candidate_id, c.mclass.value, c.scope, c.key, c.value, c.source,
                 json.dumps(list(c.evidence)), json.dumps(list(c.contradictions)), c.first_seen, c.last_seen),
            )
            self._db.commit()
            return c

    def note(self, value: str, *, key: str | None = None, scope: str = "global",
             mclass: MemoryClass = MemoryClass.FACT, source: str = "model") -> Candidate:
        """Quarantine a note (the model's ``remember``). A keyless note gets a
        content-derived key. It stays here until the operator confirms it."""
        value = (value or "").strip()
        k = (key or "").strip() or "note:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return self.upsert(make_candidate(mclass, scope, k, value, source=source, now=self._clock()))

    def get(self, cid: str) -> Candidate | None:
        with self._lock:
            return self._get(cid)

    def items(self, *, mclass: MemoryClass | None = None, scope: str | None = None) -> list[Candidate]:
        """Candidates, most recently seen first."""
        q, args = "SELECT * FROM candidates", []
        where = []
        if mclass is not None:
            where.append("mclass=?")
            args.append(MemoryClass(mclass).value)
        if scope is not None:
            where.append("scope=?")
            args.append(scope)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY last_seen DESC, candidate_id"
        with self._lock:
            return [self._row(r) for r in self._db.execute(q, args).fetchall()]

    def remove(self, cid: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM candidates WHERE candidate_id=?", (cid,))
            self._db.commit()
            return cur.rowcount > 0

    def expire(self, now: float | None = None) -> int:
        """Drop candidates not seen within the TTL. Returns how many."""
        t = float(now) if now is not None else float(self._clock())
        with self._lock:
            cur = self._db.execute("DELETE FROM candidates WHERE last_seen < ?", (t - self._ttl,))
            self._db.commit()
            return cur.rowcount

    def close(self) -> None:
        self._db.close()

    # -- internals (lock held) ---------------------------------------------------

    def _make_room(self) -> None:
        (n,) = self._db.execute("SELECT COUNT(*) FROM candidates").fetchone()
        if n >= self._max:
            self._db.execute(
                "DELETE FROM candidates WHERE candidate_id IN ("
                " SELECT candidate_id FROM candidates ORDER BY last_seen ASC, candidate_id LIMIT ?)",
                (n - self._max + 1,),
            )

    def _get(self, cid: str) -> Candidate | None:
        row = self._db.execute("SELECT * FROM candidates WHERE candidate_id=?", (cid,)).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(r) -> Candidate:
        return Candidate(r[0], MemoryClass(r[1]), r[2], r[3], r[4], r[5],
                         tuple(json.loads(r[6])), tuple(json.loads(r[7])), float(r[8]), float(r[9]))


# ---- distilling cautions from episodes -----------------------------------------


@dataclass(frozen=True)
class Tally:
    failure_runs: frozenset[str]
    success_runs: frozenset[str]


def tally(episodes) -> dict[str, Tally]:
    """Per action key: the runs in which it failed / had no effect, and the runs
    in which it succeeded. Only usable (finished, verified) episodes count, and
    each run counts once per key however many times the action repeated."""
    fail: dict[str, set[str]] = {}
    ok: dict[str, set[str]] = {}
    for ep in _episodes(episodes):
        if not ep.usable:
            continue
        for s in ep.steps:
            if not s.action_key:
                continue
            if s.outcome in FAILURE_OUTCOMES:
                fail.setdefault(s.action_key, set()).add(ep.run_id)
            elif s.outcome in SUCCESS_OUTCOMES:
                ok.setdefault(s.action_key, set()).add(ep.run_id)
    return {k: Tally(frozenset(fail.get(k, ())), frozenset(ok.get(k, ()))) for k in set(fail) | set(ok)}


def caution_value(action_key: str) -> str:
    return f"action {action_key[:16]} failed or had no effect in earlier runs"


def extract_cautions(episodes, *, scope: str = "global", now: float | None = None) -> list[Candidate]:
    """A CAUTION candidate for every action key that failed in at least one
    usable run (its successes recorded as contradictions)."""
    t = float(now) if now is not None else time.time()
    out = []
    for key, tl in sorted(tally(episodes).items()):
        if tl.failure_runs:
            out.append(make_candidate(MemoryClass.CAUTION, scope, key, caution_value(key),
                                      source="consolidation", evidence=tl.failure_runs,
                                      contradictions=tl.success_runs, now=t))
    return out


def _episodes(episodes) -> list[Episode]:
    return list(episodes.values()) if isinstance(episodes, dict) else list(episodes)


__all__ = [
    "MemoryClass",
    "SOURCES",
    "SecretRefused",
    "looks_like_secret",
    "candidate_id",
    "validate",
    "Candidate",
    "make_candidate",
    "CandidateStore",
    "Tally",
    "tally",
    "caution_value",
    "extract_cautions",
    "FAILURE_OUTCOMES",
    "SUCCESS_OUTCOMES",
]
