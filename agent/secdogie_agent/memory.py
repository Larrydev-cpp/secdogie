"""Persistent cross-run memory for the agent, backed by a small SQLite file.

The loop is otherwise stateless between runs -- each invocation starts fresh.
Point it at a memory file (`--memory`) and it can carry durable facts forward:
where a control lives, a preference it confirmed, how far it got on a long job.
The model writes with the `remember` action; the loop injects a recalled block
into the model's prompt on later runs so it reads what it learned before.

What the model writes is held UNCONFIRMED until the operator confirms it
(`secdogie-agent memory confirm KEY --memory FILE`): only confirmed facts are
rendered into a prompt. The model may be quoting text off another application's
screen, and an unreviewed note re-injected into every later run would let that
text steer the agent indefinitely. Rows written before confirmation existed
count as confirmed (the operator chose to keep behaviour unchanged for them);
changing a fact makes it unconfirmed again.

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
    confirmed: bool = True


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
        cols = {row[1] for row in self._db.execute("PRAGMA table_info(memories)")}
        if "confirmed" not in cols:
            # Migration: rows that predate confirmation stay usable (DEFAULT 1);
            # everything remember() writes from now on starts unconfirmed.
            self._db.execute("ALTER TABLE memories ADD COLUMN confirmed INTEGER NOT NULL DEFAULT 1")
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
        # Unconfirmed, and a changed fact becomes unconfirmed again -- unless the
        # value is exactly what was already confirmed.
        self._db.execute(
            "INSERT INTO memories(key, value, updated_at, confirmed) VALUES(?, ?, ?, 0) "
            "ON CONFLICT(key) DO UPDATE SET updated_at=excluded.updated_at, "
            "confirmed=CASE WHEN memories.value = excluded.value THEN memories.confirmed ELSE 0 END, "
            "value=excluded.value",
            (stored_key, value, self._now()),
        )
        self._db.commit()
        return stored_key

    def recall(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM memories WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def confirm(self, key: str) -> bool:
        """The operator confirms ``key``: it will be rendered into prompts."""
        cur = self._db.execute("UPDATE memories SET confirmed=1 WHERE key=?", (key,))
        self._db.commit()
        return cur.rowcount > 0

    def forget(self, key: str) -> bool:
        cur = self._db.execute("DELETE FROM memories WHERE key=?", (key,))
        self._db.commit()
        return cur.rowcount > 0

    def items(self, *, confirmed_only: bool = False) -> list[MemoryItem]:
        """Every memory (or only confirmed ones), newest first (ties broken by
        key for a stable order)."""
        q = "SELECT key, value, updated_at, confirmed FROM memories"
        if confirmed_only:
            q += " WHERE confirmed=1"
        rows = self._db.execute(q + " ORDER BY updated_at DESC, key").fetchall()
        return [MemoryItem(k, v, t, bool(c)) for (k, v, t, c) in rows]

    def render(self, *, limit: int = 20, max_chars: int = 2000) -> str:
        """A compact, newest-first block of CONFIRMED memories for the model's
        prompt, or "" if there are none.
        Keyed facts render as `key: value`; auto-notes as `- value`. Capped to
        `limit` items and `max_chars` characters so a growing memory can't blow
        up every prompt."""
        rendered = []
        for item in self.items(confirmed_only=True)[:limit]:
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


def admin_main(argv: list[str]) -> int:
    """``secdogie-agent memory list|confirm KEY|forget KEY --memory FILE``: the
    operator reviews what the model asked to remember."""
    import argparse

    parser = argparse.ArgumentParser(prog="secdogie-agent memory",
                                     description="Review the agent's remembered facts.")
    parser.add_argument("op", choices=("list", "confirm", "forget"))
    parser.add_argument("key", nargs="?")
    parser.add_argument("--memory", required=True, help="the SQLite memory file the agent uses")
    args = parser.parse_args(argv)
    mem = Memory(args.memory)
    try:
        if args.op == "list":
            for item in mem.items():
                mark = "confirmed  " if item.confirmed else "UNCONFIRMED"
                print(f"{mark}  {item.key}: {item.value}")
            return 0
        if not args.key:
            parser.error(f"{args.op} needs a KEY")
        done = mem.confirm(args.key) if args.op == "confirm" else mem.forget(args.key)
        if not done:
            print(f"no memory with key {args.key!r}")
            return 1
        print(f"{args.op}ed {args.key}" if args.op == "confirm" else f"forgot {args.key}")
        return 0
    finally:
        mem.close()
