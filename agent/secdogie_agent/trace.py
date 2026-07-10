"""A tamper-evident execution trace: an append-only, hash-chained log of every
decision the agent made.

Each step records WHAT the model saw (a SHA-256 of the exact screenshot), WHAT
it decided (the action + its reasoning), and WHAT happened (the result), stamped
with a time and a sequence number. Every entry commits to the previous one's
hash, so the entries form a chain: altering, reordering, or dropping any past
entry changes its hash and breaks every entry after it. The final entry's hash
(`head`) is therefore a single commitment to the *entire* ordered history --
print it, sign it, or publish it at run time and anyone can later re-derive the
chain and prove the trace wasn't edited.

Honest scope: the chain proves internal *consistency* -- no entry was changed
without recomputing all following hashes. It is not a signature: a party who can
rewrite the whole file can also recompute a fresh valid chain. Tamper-evidence
therefore requires anchoring the head somewhere the rewriter doesn't control
(print it to a monitored log, sign it with a key, commit it externally).
Truncation from the *end* is likewise only detectable against a known head/length.
A Merkle tree would add per-entry inclusion proofs; the chain is the simpler tool
that fits an ordered, append-only log, so that's what this is.

`python -m secdogie_agent.trace <trace.jsonl>` verifies a saved trace.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

# prev_hash of the very first entry -- a fixed anchor so the first link is
# checkable too (a real hash could never be all zeros).
GENESIS = "0" * 64

_PAYLOAD_KEYS = ("seq", "ts", "frame_sha256", "action", "reasoning", "result", "prev_hash")


def _hash_payload(payload: dict) -> str:
    """SHA-256 over a canonical (sorted-key, tight) JSON encoding, so the hash is
    reproducible byte-for-byte by any verifier regardless of dict order."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class TraceEntry:
    seq: int
    ts: float
    frame_sha256: str  # SHA-256 of the screenshot the decision was made on
    action: dict  # the action taken (kind + resolved coords + the model's raw JSON)
    reasoning: str
    result: str
    prev_hash: str
    entry_hash: str

    def payload(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "frame_sha256": self.frame_sha256,
            "action": self.action,
            "reasoning": self.reasoning,
            "result": self.result,
            "prev_hash": self.prev_hash,
        }

    def to_dict(self) -> dict:
        return {**self.payload(), "entry_hash": self.entry_hash}


class ExecutionTrace:
    """Accumulates hash-chained entries and (optionally) appends each to a JSONL
    file as it's recorded, so the trace survives a crash mid-run."""

    def __init__(self, path: str | None = None, *, clock=time.time):
        self.path = path
        self._clock = clock
        self.entries: list[TraceEntry] = []
        self.head = GENESIS
        self._seq = 0
        if path is not None:
            open(path, "w", encoding="utf-8").close()  # start each run with a fresh file

    def record(self, frame_bytes: bytes, action: dict, reasoning: str, result: str) -> TraceEntry:
        self._seq += 1
        payload = {
            "seq": self._seq,
            "ts": self._clock(),
            "frame_sha256": hashlib.sha256(frame_bytes).hexdigest(),
            "action": action,
            "reasoning": reasoning,
            "result": result,
            "prev_hash": self.head,
        }
        entry = TraceEntry(**payload, entry_hash=_hash_payload(payload))
        self.entries.append(entry)
        self.head = entry.entry_hash
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return entry


def verify_entries(entries: list[dict]) -> tuple[bool, str | None]:
    """Re-derive the chain and confirm nothing was altered. Returns (ok, reason);
    `reason` names the first broken entry when ok is False."""
    prev = GENESIS
    for i, e in enumerate(entries):
        if any(k not in e for k in (*_PAYLOAD_KEYS, "entry_hash")):
            return False, f"entry {i}: missing fields"
        if e["seq"] != i + 1:
            return False, f"entry {i}: seq {e['seq']} out of order (expected {i + 1})"
        if e["prev_hash"] != prev:
            return False, f"entry {i}: prev_hash does not match the previous entry (chain broken)"
        if _hash_payload({k: e[k] for k in _PAYLOAD_KEYS}) != e["entry_hash"]:
            return False, f"entry {i}: content does not match its hash (entry was edited)"
        prev = e["entry_hash"]
    return True, None


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def verify_file(path: str) -> tuple[bool, str | None]:
    return verify_entries(load(path))


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m secdogie_agent.trace <trace.jsonl>", file=sys.stderr)
        return 2
    ok, reason = verify_file(args[0])
    if ok:
        entries = load(args[0])
        head = entries[-1]["entry_hash"] if entries else GENESIS
        print(f"trace OK: {len(entries)} entry(ies), chain intact. head={head}")
        return 0
    print(f"trace TAMPERED: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
