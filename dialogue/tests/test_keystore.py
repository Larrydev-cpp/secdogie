"""The operator key at rest: Argon2id + SecretBox, 0600, never overwritten, and
refused on a wrong passphrase, tampering, a mismatched DID or a runaway KDF cost.
Low Argon2id cost in tests only, so they stay fast."""
from __future__ import annotations

import base64
import json
import os
import stat

import pytest

pytest.importorskip("nacl")

from nacl import pwhash
from secdogie_dialogue.keystore import KeystoreError, keystore_did, seal_identity, unseal_identity
from secdogie_identity import Identity

FAST = dict(opslimit=pwhash.argon2id.OPSLIMIT_MIN, memlimit=pwhash.argon2id.MEMLIMIT_MIN)
PW = b"correct horse battery staple"


def _sealed(tmp_path, ident=None):
    ident = ident or Identity.generate()
    path = tmp_path / "operator.keystore"
    seal_identity(ident, PW, path, **FAST)
    return ident, path


def test_round_trip_and_the_seed_is_not_on_disk(tmp_path):
    ident, path = _sealed(tmp_path)
    assert unseal_identity(path, PW).did == ident.did
    assert unseal_identity(path, PW, expected_did=ident.did).did == ident.did
    assert keystore_did(path) == ident.did
    text = path.read_text()
    assert ident.seed_b64 not in text
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_wrong_passphrase_is_refused(tmp_path):
    _, path = _sealed(tmp_path)
    with pytest.raises(KeystoreError, match="wrong passphrase"):
        unseal_identity(path, b"guess")


def test_tampered_ciphertext_is_refused(tmp_path):
    _, path = _sealed(tmp_path)
    doc = json.loads(path.read_text())
    raw = bytearray(base64.b64decode(doc["box"]))
    raw[-1] ^= 1
    doc["box"] = base64.b64encode(bytes(raw)).decode()
    path.write_text(json.dumps(doc))
    with pytest.raises(KeystoreError, match="tampered"):
        unseal_identity(path, PW)


def test_a_keystore_whose_did_was_swapped_is_refused(tmp_path):
    _, path = _sealed(tmp_path)
    doc = json.loads(path.read_text())
    doc["did"] = Identity.generate().did
    path.write_text(json.dumps(doc))
    with pytest.raises(KeystoreError, match="does not match"):
        unseal_identity(path, PW)


def test_expected_operator_is_enforced(tmp_path):
    _, path = _sealed(tmp_path)
    with pytest.raises(KeystoreError, match="different operator"):
        unseal_identity(path, PW, expected_did=Identity.generate().did)


def _rewrite(path, **fields):
    doc = json.loads(path.read_text())
    doc.update(fields)
    path.write_text(json.dumps(doc))


def test_a_file_cannot_demand_a_runaway_kdf(tmp_path):
    # opslimit first: cheap to evaluate even if the range check were missing
    _, path = _sealed(tmp_path)
    _rewrite(path, opslimit=pwhash.argon2id.OPSLIMIT_SENSITIVE + 1)
    with pytest.raises(KeystoreError, match="accepted range"):
        unseal_identity(path, PW)
    path = tmp_path / "k2"
    seal_identity(Identity.generate(), PW, path, **FAST)
    _rewrite(path, memlimit=64 * 1024 ** 3)  # 64 GiB
    with pytest.raises(KeystoreError, match="accepted range"):
        unseal_identity(path, PW)


def test_sealing_refuses_an_out_of_range_cost(tmp_path):
    with pytest.raises(KeystoreError, match="accepted range"):
        seal_identity(Identity.generate(), PW, tmp_path / "k",
                      opslimit=pwhash.argon2id.OPSLIMIT_SENSITIVE + 1, memlimit=pwhash.argon2id.MEMLIMIT_MIN)
    assert not (tmp_path / "k").exists()


def test_malformed_fields_are_refused(tmp_path):
    _, path = _sealed(tmp_path)
    _rewrite(path, salt=base64.b64encode(b"short").decode())
    with pytest.raises(KeystoreError, match="salt"):
        unseal_identity(path, PW)
    seal_identity(Identity.generate(), PW, tmp_path / "k2", **FAST)
    _rewrite(tmp_path / "k2", opslimit="3")
    with pytest.raises(KeystoreError, match="malformed"):
        unseal_identity(tmp_path / "k2", PW)


def test_never_overwrites_and_never_seals_with_an_empty_passphrase(tmp_path):
    ident, path = _sealed(tmp_path)
    before = path.read_text()
    with pytest.raises(KeystoreError, match="already exists"):
        seal_identity(Identity.generate(), PW, path, **FAST)
    assert path.read_text() == before
    with pytest.raises(KeystoreError, match="empty passphrase"):
        seal_identity(ident, b"", tmp_path / "other", **FAST)


def test_not_a_keystore(tmp_path):
    p = tmp_path / "x"
    p.write_text('{"type": "something-else"}')
    with pytest.raises(KeystoreError, match="not an operator keystore"):
        unseal_identity(p, PW)
