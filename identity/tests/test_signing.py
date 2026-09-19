from __future__ import annotations

import json

from secdogie_identity import Allowlist, Identity, sign_payload, verify_payload


def test_sign_verify_roundtrip():
    idn = Identity.generate()
    signed = sign_payload(idn, {"kind": "hello", "node_id": "n1"})
    assert signed["signer"] == idn.did
    ok, signer = verify_payload(signed)
    assert ok and signer == idn.did


def test_tamper_any_field_fails():
    idn = Identity.generate()
    signed = sign_payload(idn, {"kind": "assign", "task": "build"})
    signed["task"] = "rm -rf"
    ok, signer = verify_payload(signed)
    assert not ok and signer is None


def test_key_order_independent():
    # The signature is over canonical (sorted) JSON, so re-serializing the object
    # with shuffled key order must still verify.
    idn = Identity.generate()
    signed = sign_payload(idn, {"b": 2, "a": 1, "z": 3})
    reserialized = json.loads(json.dumps({k: signed[k] for k in reversed(list(signed))}))
    ok, _ = verify_payload(reserialized)
    assert ok


def test_wrong_signer_did_fails():
    a, b = Identity.generate(), Identity.generate()
    signed = sign_payload(a, {"kind": "status"})
    signed["signer"] = b.did  # claim a different author than actually signed
    ok, signer = verify_payload(signed)
    assert not ok and signer is None


def test_missing_envelope_fails():
    ok, signer = verify_payload({"kind": "hello"})
    assert not ok and signer is None


def test_allowlist_gate():
    authorized, stranger = Identity.generate(), Identity.generate()
    allow = Allowlist({authorized.did})

    good = sign_payload(authorized, {"kind": "hello"})
    ok, signer = verify_payload(good, allow)
    assert ok and signer == authorized.did

    # Authentic signature, but the signer is not authorized: (False, did) so the
    # caller can log exactly who was refused.
    outsider = sign_payload(stranger, {"kind": "hello"})
    ok, signer = verify_payload(outsider, allow)
    assert not ok and signer == stranger.did


def test_sign_rejects_preexisting_envelope():
    idn = Identity.generate()
    signed = sign_payload(idn, {"kind": "hello"})
    try:
        sign_payload(idn, signed)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError re-signing an already-signed payload")
