from __future__ import annotations

import base64
import os

import pytest
from secdogie_identity import (
    Allowlist,
    Identity,
    create_binding,
    is_superseded,
    session_did,
    verify_binding,
)


def _transport_pk() -> str:
    """A base64 32-byte value standing in for an X25519 transport public key."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


def test_valid_binding_verifies():
    idn = Identity.generate()
    tpk = _transport_pk()
    b = create_binding(idn, tpk, key_version=1)
    res = verify_binding(b)
    assert res.ok
    assert res.did == idn.did
    assert res.transport_public_key == tpk
    assert res.key_version == 1


def test_tampered_binding_fails():
    idn = Identity.generate()
    b = create_binding(idn, _transport_pk(), key_version=1)
    b["transport_public_key"] = _transport_pk()  # swap the key after signing
    res = verify_binding(b)
    assert not res.ok


def test_wrong_did_field_fails():
    a, b_id = Identity.generate(), Identity.generate()
    b = create_binding(a, _transport_pk(), key_version=1)
    b["did"] = b_id.did  # claim a different DID than the signer
    res = verify_binding(b)
    assert not res.ok
    assert "did" in (res.reason or "").lower() or "signature" in (res.reason or "").lower()


def test_wrong_type_rejected():
    idn = Identity.generate()
    b = create_binding(idn, _transport_pk(), key_version=1)
    b2 = dict(b)
    b2["type"] = "something/else"
    assert not verify_binding(b2).ok  # signature no longer matches, and type is wrong


def test_not_yet_valid_and_expired():
    idn = Identity.generate()
    tpk = _transport_pk()
    b = create_binding(idn, tpk, key_version=1, valid_from=1000.0, expires_at=2000.0)
    assert not verify_binding(b, now=999.0).ok       # before valid_from
    assert verify_binding(b, now=1500.0).ok          # inside window
    assert not verify_binding(b, now=2000.0).ok      # at expiry (half-open)
    assert not verify_binding(b, now=2001.0).ok      # after expiry


def test_key_rotation_supersedes():
    idn = Identity.generate()
    v1 = create_binding(idn, _transport_pk(), key_version=1)
    v2 = create_binding(idn, _transport_pk(), key_version=2)
    assert is_superseded(v1, v2)
    assert not is_superseded(v2, v1)
    # a different DID's higher version does not supersede
    other = create_binding(Identity.generate(), _transport_pk(), key_version=9)
    assert not is_superseded(v1, other)


def test_allowlist_gate():
    idn, stranger = Identity.generate(), Identity.generate()
    allow = Allowlist({idn.did})
    assert verify_binding(create_binding(idn, _transport_pk(), key_version=1), allowlist=allow).ok
    assert not verify_binding(create_binding(stranger, _transport_pk(), key_version=1), allowlist=allow).ok


def test_session_did_matches_only_the_bound_transport_key():
    idn = Identity.generate()
    tpk = _transport_pk()
    b = create_binding(idn, tpk, key_version=1)
    # the handshake authenticated the SAME transport key -> session belongs to the DID
    assert session_did(b, tpk) == idn.did
    # a different authenticated key -> not this DID's session
    assert session_did(b, _transport_pk()) is None
    # an expired binding -> no session identity
    b_exp = create_binding(idn, tpk, key_version=1, valid_from=0.0, expires_at=1.0)
    assert session_did(b_exp, tpk, now=100.0) is None


def test_create_rejects_bad_inputs():
    idn = Identity.generate()
    with pytest.raises(ValueError):
        create_binding(idn, base64.b64encode(os.urandom(16)).decode(), key_version=1)  # not 32 bytes
    with pytest.raises(ValueError):
        create_binding(idn, _transport_pk(), key_version=-1)
    with pytest.raises(ValueError):
        create_binding(idn, _transport_pk(), key_version=1, valid_from=100.0, expires_at=50.0)
