from __future__ import annotations

import pytest
from secdogie_identity import did


def test_roundtrip_pubkey_did():
    pk = bytes(range(32))
    d = did.did_key_from_pubkey(pk)
    assert d.startswith("did:key:z")
    assert did.pubkey_from_did(d) == pk


def test_known_answer_all_zero_key():
    # The all-zero Ed25519 key is a fixed, checkable did:key vector:
    # multicodec 0xed01 + 32 zero bytes, base58btc, 'z' multibase tag.
    d = did.did_key_from_pubkey(b"\x00" * 32)
    assert d == "did:key:z6MkeTG3bFFSLYVU7VqhgZxqr6YzpaGrQtFMh1uvqGy1vDnP"
    assert did.pubkey_from_did(d) == b"\x00" * 32


def test_reject_wrong_length():
    with pytest.raises(ValueError):
        did.did_key_from_pubkey(b"\x00" * 31)


def test_reject_non_did_key():
    with pytest.raises(ValueError):
        did.pubkey_from_did("did:example:123")
    with pytest.raises(ValueError):
        did.pubkey_from_did("not-a-did")


def test_reject_wrong_multicodec():
    # An X25519 did:key (multicodec 0xec01) must be rejected as not-Ed25519.
    from secdogie_identity.did import _b58encode

    x25519 = "did:key:z" + _b58encode(b"\xec\x01" + b"\x00" * 32)
    with pytest.raises(ValueError):
        did.pubkey_from_did(x25519)


def test_reject_bad_base58_char():
    with pytest.raises(ValueError):
        did.pubkey_from_did("did:key:z0OIl")  # 0, O, I, l are not in the alphabet


def test_did_document_shape():
    d = did.did_key_from_pubkey(bytes(range(32)))
    doc = did.did_document(d)
    assert doc["id"] == d
    vm = doc["verificationMethod"][0]
    assert vm["type"] == "Ed25519VerificationKey2020"
    assert vm["controller"] == d
    assert vm["id"] in doc["authentication"]
    assert vm["id"] in doc["assertionMethod"]
