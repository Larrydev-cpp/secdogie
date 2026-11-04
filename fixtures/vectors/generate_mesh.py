"""Golden vectors for the inference mesh, shared with the browser node
(symbiont/src/mesh/). Python is the reference: every object below is made by the
production functions of secdogie-inference and secdogie-identity; TypeScript
must verify each one, reproduce its canonical bytes and digest, and -- for the
objects a browser node signs itself (heartbeats, rtc-hello) -- produce the same
bytes from the same key and inputs.

It needs only secdogie-identity and secdogie-inference (the other generator,
generate.py, needs citadel and dialogue), so the inference test suite checks
it for drift: inference/tests/test_mesh_vectors.py.

    python fixtures/vectors/generate_mesh.py          # rewrite mesh.json
    python fixtures/vectors/generate_mesh.py --check  # exit 1 if it is stale

Every float, and every integer past 2**53 (heartbeat seqs), appears only inside
JSON text strings, so a plain JSON parser never loses the int/float distinction.
The seeds are public test keys. Never use them for anything real.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from secdogie_identity import Identity, sign_payload
from secdogie_identity.signing import canonical
from secdogie_inference.admission import make_admission
from secdogie_inference.lease import make_lease
from secdogie_inference.merkle import merkle_root
from secdogie_inference.mutiny import DECIDER_SELF_REPORTED, decider_target, make_incident, make_reset
from secdogie_inference.protocol import (
    GENESIS,
    HEAD,
    MODE_NORMAL,
    REQUEST,
    VIA_MESH,
    digest,
    frame,
    make_decision,
    make_heartbeat,
    make_response,
)
from secdogie_inference.rtc import lobby_room, make_hello

HERE = Path(__file__).resolve().parent
T0 = 1_790_000_000.5      # a fixed clock
SEQ0 = 1_790_000_000_123_456_789   # past 2**53, like a time_ns()-seeded seq


def _identity(label: str) -> tuple[Identity, str]:
    seed = hashlib.sha256(f"secdogie/vectors/mesh/{label}".encode()).digest()
    return Identity.from_seed_b64(base64.b64encode(seed).decode("ascii")), seed.hex()


def _text(obj) -> str:
    return canonical(obj).decode("utf-8")


def _entry(obj) -> dict:
    return {"text": _text(obj), "digest": digest(obj)}


def mesh_vectors() -> dict:
    names = ("browser", "server", "provider", "broker", "user", "operator")
    ids, seeds = {}, {}
    for n in names:
        ids[n], seeds[n] = _identity(n)
    did = {n: i.did for n, i in ids.items()}
    clock = lambda: T0  # noqa: E731

    # The server's heartbeat, which the browser hears directly and witnesses.
    server_hb = make_heartbeat(ids["server"], roles=["forward", "serve"], seq=SEQ0, prev=GENESIS,
                               neighbors={did["browser"]: (0, "")}, load=0.25, ledger_root=GENESIS, clock=clock)
    # The browser's own chain: the first heartbeat (genesis), then one that links it and
    # witnesses the server's heartbeat by seq and digest head.
    b1 = make_heartbeat(ids["browser"], roles=["forward"], seq=SEQ0 + 7, prev=GENESIS,
                        neighbors={did["server"]: (0, "")}, load=0.0, ledger_root=GENESIS, clock=clock)
    b2 = make_heartbeat(ids["browser"], roles=["forward"], seq=SEQ0 + 8, prev=digest(b1),
                        neighbors={did["server"]: (SEQ0, digest(server_hb)[:HEAD]),
                                   did["provider"]: (0, "")},
                        load=0.0, ledger_root=GENESIS, clock=lambda: T0 + 2.0)

    request = sign_payload(ids["user"], {
        "type": REQUEST, "req_id": "0123456789abcdef0123456789abcdef", "user": did["user"],
        "models": ["m/a"], "input": "hello over the browser", "scopes": [], "quorum": 1, "pins": [],
        "issued_at": T0, "expires_at": T0 + 60.0})
    leg = {"provider": did["provider"], "model": "m/a", "via": VIA_MESH,
           "path": [did["server"], did["browser"], did["provider"]]}
    decision = make_decision(ids["server"], request, [leg], mode=MODE_NORMAL, epoch=0, clock=clock)
    lease = make_lease(ids["broker"], did["provider"], "m/a", key_version=1, canary="sdgc_" + "0" * 32,
                       ttl=600, clock=clock)
    response = make_response(ids["provider"], request, decision, "m/a", lease, output="answer(hello)",
                             clock=clock)

    admission = make_admission(ids["operator"], did["browser"], ttl=86400, clock=clock)
    room = lobby_room(did["server"], "mesh-salt")
    fp_browser = "sha-256 " + ":".join(f"{b:02X}" for b in hashlib.sha256(b"browser-cert").digest())
    fp_server = "sha-256 " + ":".join(f"{b:02X}" for b in hashlib.sha256(b"server-cert").digest())
    hello = make_hello(ids["browser"], room=room, local_fp=fp_browser, remote_fp=fp_server, admission=admission,
                       clock=clock)

    incident = make_incident(ids["server"], DECIDER_SELF_REPORTED,
                             {"reporter": did["server"], "proposal": {"legs": []}, "violation": "quorum-downgrade"},
                             clock=clock)
    reset = make_reset(ids["operator"], [decider_target(did["server"])], [incident["incident_id"]], clock=clock)

    merkle = []
    for leaves in ([], ["a"], ["a", "b", "c"], sorted(f"decider:{did['server']}|{i:064x}" for i in range(5))):
        merkle.append({"leaves": leaves, "root": merkle_root(leaves)})

    req_frame = frame("req", request=request, decision=decision, leg=0, hop=1)
    return {
        "identities": {n: {"seed": seeds[n], "did": did[n]} for n in names},
        "heartbeats": {
            "server": _entry(server_hb),
            "browser_first": {**_entry(b1), "seq": str(SEQ0 + 7), "issued_at": repr(T0)},
            "browser_second": {**_entry(b2), "seq": str(SEQ0 + 8), "issued_at": repr(T0 + 2.0)},
        },
        "request": _entry(request),
        "decision": _entry(decision),
        "lease": _entry(lease),
        "response": _entry(response),
        "admission": _entry(admission),
        "hello": {**_entry(hello), "room": room, "local_fp": fp_browser, "remote_fp": fp_server,
                  "issued_at": repr(T0)},
        "incident": {**_entry(incident), "incident_id": incident["incident_id"]},
        "reset": _entry(reset),
        "lobby_rooms": [{"did": did[n], "salt": salt, "room": lobby_room(did[n], salt)}
                        for n in ("server", "provider") for salt in ("", "mesh-salt")],
        "merkle": merkle,
        "frames": {"req": req_frame.decode("utf-8")},
    }


def build() -> str:
    return json.dumps(mesh_vectors(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    path = HERE / "mesh.json"
    text = build()
    if "--check" in argv:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            print("stale vector file mesh.json (run fixtures/vectors/generate_mesh.py)", file=sys.stderr)
            return 1
        return 0
    path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
