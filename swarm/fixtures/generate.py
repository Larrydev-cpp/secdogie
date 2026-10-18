"""Golden vectors for the swarm wire/task/result canonical JSON.

Python is the reference. A TypeScript contributor node and the Workers-side
dispatcher read `swarm_vectors.json` and must agree byte for byte: the same task
signs to the same bytes, the same output hashes to the same digest, the same
wire message encodes to the same string. Every timestamp here is an integer
(milliseconds), so no float ever appears and the canonical bytes are
unambiguous across languages.

    python swarm/fixtures/generate.py          # rewrite swarm_vectors.json
    python swarm/fixtures/generate.py --check   # exit 1 if it is stale

The seeds are public test keys. Never use them for anything real.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from secdogie_identity import Identity, canonical
from secdogie_swarm import build_crawl, build_result, output_digest, sign_task, wire

HERE = Path(__file__).resolve().parent
OUT = HERE / "swarm_vectors.json"

ISSUED_MS = 1700000000000
PRODUCED_MS = 1700000001000
SENT_MS = 1700000002000
NONCE = "nonce-0001"


def _identity(label: str) -> Identity:
    seed = hashlib.sha256(f"secdogie/swarm-vectors/{label}".encode()).digest()
    return Identity.from_seed_b64(base64.b64encode(seed).decode("ascii"))


def _state_key(origin: str, route: str, query_keys: list[str]) -> str:
    """The state key exactly as graph/src/delta.rs computes it (same canonical)."""
    v = {"type": "secdogie/state-key/v1", "origin": origin, "route": route, "query_keys": query_keys}
    return hashlib.sha256(canonical(v)).hexdigest()


def _payload_canonical(signed: dict) -> str:
    return canonical({k: v for k, v in signed.items() if k not in ("signer", "sig")}).decode("utf-8")


def build() -> dict:
    core = _identity("core")
    node = _identity("node")

    task = build_crawl(
        task_id="task-0001",
        url="https://docs.example.com/",
        allowed_origins=["https://docs.example.com"],
        issued_at_ms=ISSUED_MS,
        deadline_ms=10000,
        redundancy=3,
        epoch=1,
    )
    signed_task = sign_task(core, task)

    doc_key = _state_key("https://docs.example.com", "/", [])
    cand_key = _state_key("https://docs.example.com", "/account/delete", [])
    output = {
        "kind": "page",
        "url": "https://docs.example.com/",
        "status": 200,
        "content_hash": "a" * 64,
        "byte_len": 1234,
        "truncated": False,
        "document": {"origin": "https://docs.example.com", "route": "/", "query_keys": [], "key": doc_key},
        "candidates": [
            {
                "origin": "https://docs.example.com",
                "route": "/account/delete",
                "query_keys": [],
                "key": cand_key,
                "via": "a",
                "method": "get",
                "fields": [],
            }
        ],
    }
    result = build_result(task_id="task-0001", node_did=node.did, output=output, produced_at_ms=PRODUCED_MS)
    from secdogie_identity import sign_payload

    signed_result = sign_payload(node, result)

    messages = [
        ("dispatch", wire.dispatch(signed_task)),
        ("dispatch_resume", wire.dispatch(signed_task, resume_from={"pages_done": ["/a", "/b"]})),
        ("result", wire.result(signed_result)),
        ("heartbeat", wire.heartbeat(node.did)),
        ("probe", wire.probe(NONCE, SENT_MS)),
        ("pong", wire.pong(node.did, NONCE)),
        ("checkpoint", wire.checkpoint("task-0001", node.did, {"pages_done": ["/a", "/b"]})),
        ("cancel", wire.cancel("task-0001", "migrated")),
    ]

    return {
        "note": "Python is the reference. TS/Workers must agree byte for byte.",
        "identities": {"core": core.did, "node": node.did},
        "task": {"signed": signed_task, "payload_canonical": _payload_canonical(signed_task)},
        "result": {
            "signed": signed_result,
            "payload_canonical": _payload_canonical(signed_result),
            "output_digest": output_digest(output),
        },
        "digests": [
            {"output": {"content_hash": "abc"}, "digest": output_digest({"content_hash": "abc"})},
            {"output": output, "digest": output_digest(output)},
        ],
        "wire": {name: {"message": m, "canonical": wire.encode(m).decode("utf-8")} for name, m in messages},
    }


def _text() -> str:
    return json.dumps(build(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = _text()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is stale; run: python {Path(__file__).name}", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
