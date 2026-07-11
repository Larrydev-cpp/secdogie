"""Persistent cross-run memory for the agent, backed by a small SQLite file.

The loop is otherwise stateless between runs -- each invocation starts fresh.
Point it at a memory file (`--memory`) and it can carry durable facts forward:
where a control lives, a preference it confirmed, how far it got on a long job.
The model writes with the `remember` action; the loop injects a recalled block
into the model's prompt on later runs so it reads what it learned before.

Plaintext on disk by design -- it's your machine, your file. NEVER store secrets
(passwords, tokens, card numbers) here. The prompt tells the model the same, and
`remember` refuses values that obviously look like credentials as a backstop --
a backstop, not a guarantee, so don't rely on it to catch everything.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryItem:
    key: str
    value: str
    updated_at: float


class SecretRefused(ValueError):
    """remember() refused a value that looks like a credential -- see the module
    docstring: memory is plaintext, so secrets must never be written to it."""


# Best-effort secret detection. This is a coarse net, not a guarantee: it catches
# the obvious cases (a key literally named "password", or a value shaped like a
# well-known API token) so the model can't casually persist a credential. Values
# that don't match still get stored, so the real rule stays "don't ask it to
# remember secrets" -- this only backstops the careless case.
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


class Memory:
    """A tiny key/value store on top of SQLite. Keyed facts upsert by key;
    keyless notes get an auto, time-ordered key. `path` may be a file or the
    `:memory:` sentinel for tests. Created and used on one thread (the loop's),
    so it keeps SQLite's default single-thread connection."""

    def __init__(self, path: str, *, now=time.time):
        self._now = now
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS memories("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        self._db.commit()

    def remember(self, value: str, *, key: str | None = None) -> str:
        """Store `value`. With `key`, upsert that key (updating an existing
        fact); without one, append a time-keyed note. Returns the key used.
        Raises SecretRefused for obvious credentials, ValueError for empty."""
        value = (value or "").strip()
        if not value:
            raise ValueError("cannot remember an empty value")
        if looks_like_secret(key, value):
            raise SecretRefused("value looks like a credential; memory is plaintext")
        # A keyless note is time-ordered so items() lists newest first; the
        # microsecond timestamp keeps rapid consecutive notes from colliding.
        stored_key = (key or "").strip() or f"note:{self._now():.6f}"
        self._db.execute(
            "INSERT INTO memories(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (stored_key, value, self._now()),
        )
        self._db.commit()
        return stored_key

    def recall(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM memories WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def forget(self, key: str) -> bool:
        cur = self._db.execute("DELETE FROM memories WHERE key=?", (key,))
        self._db.commit()
        return cur.rowcount > 0

    def items(self) -> list[MemoryItem]:
        """Every memory, newest first (ties broken by key for a stable order)."""
        rows = self._db.execute(
            "SELECT key, value, updated_at FROM memories ORDER BY updated_at DESC, key"
        ).fetchall()
        return [MemoryItem(k, v, t) for (k, v, t) in rows]

    def render(self, *, limit: int = 20, max_chars: int = 2000) -> str:
        """A compact, newest-first block for the model's prompt, or "" if empty.
        Keyed facts render as `key: value`; auto-notes as `- value`. Capped to
        `limit` items and `max_chars` characters so a growing memory can't blow
        up every prompt."""
        rendered = []
        for item in self.items()[:limit]:
            if item.key.startswith("note:"):
                rendered.append(f"- {item.value}")
            else:
                rendered.append(f"{item.key}: {item.value}")
        block = "\n".join(rendered)
        if len(block) > max_chars:
            block = block[:max_chars].rstrip() + " ..."
        return block

    def close(self) -> None:
        self._db.close()
