"""Master-signed revocation: k-of-n threshold, forgery resistance, tamper checks,
and master-set file parsing."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Identity
from secdogie_identity.revocation import (
    MAX_REVOKED,
    MasterSet,
    cosign,
    create_revocation,
    verify_revocation,
)


def _masters(n, threshold=1):
    ids = [Identity.generate() for _ in range(n)]
    return ids, MasterSet([i.did for i in ids], threshold)


def test_single_master_k1_is_valid():
    (m,), masters = _masters(1)
    target = Identity.generate().did
    rec = cosign(m, create_revocation([target], reason="lost laptop"))
    result = verify_revocation(rec, masters)
    assert result is not None
    assert result.revoked == {target} and result.signers == {m.did}
    assert result.reason == "lost laptop"


def test_threshold_2_of_3():
    ids, masters = _masters(3, threshold=2)
    target = Identity.generate().did
    rec = create_revocation([target])
    one = cosign(ids[0], rec)
    assert verify_revocation(one, masters) is None          # one signature: short of 2
    two = cosign(ids[1], one)
    result = verify_revocation(two, masters)
    assert result is not None and result.signers == {ids[0].did, ids[1].did}


def test_non_master_signature_is_ignored():
    ids, masters = _masters(2, threshold=1)
    outsider = Identity.generate()
    target = Identity.generate().did
    rec = cosign(outsider, create_revocation([target]))
    assert verify_revocation(rec, masters) is None          # signed, but not by a master
    # a master signature alongside the outsider's does verify, counting only the master
    rec = cosign(ids[0], rec)
    result = verify_revocation(rec, masters)
    assert result is not None and result.signers == {ids[0].did}


def test_same_master_signing_twice_counts_once():
    ids, masters = _masters(3, threshold=2)
    target = Identity.generate().did
    rec = cosign(ids[0], create_revocation([target]))
    rec = cosign(ids[0], rec)                                # idempotent: no second signature
    assert len(rec["sigs"]) == 1
    assert verify_revocation(rec, masters) is None           # still only one distinct master


def test_a_duplicate_signer_entry_cannot_pad_the_count():
    ids, masters = _masters(3, threshold=2)
    target = Identity.generate().did
    rec = cosign(ids[0], create_revocation([target]))
    rec["sigs"].append(dict(rec["sigs"][0]))                 # forge a second copy of the one sig
    assert verify_revocation(rec, masters) is None           # distinct signers only


def test_bad_signature_is_ignored():
    ids, masters = _masters(1)
    target = Identity.generate().did
    rec = cosign(ids[0], create_revocation([target]))
    rec["sigs"][0]["sig"] = rec["sigs"][0]["sig"][:-4] + "AAAA"
    assert verify_revocation(rec, masters) is None


def test_tampered_body_is_rejected():
    ids, masters = _masters(1)
    target = Identity.generate().did
    other = Identity.generate().did
    rec = cosign(ids[0], create_revocation([target]))
    # add a victim to the revoked list without re-signing
    tampered = {**rec, "revoked": sorted({target, other})}
    assert verify_revocation(tampered, masters) is None
    # or leave the body and rewrite the id to match the new body
    tampered2 = {**rec, "reason": "changed"}
    assert verify_revocation(tampered2, masters) is None


def test_record_id_must_commit_to_the_body():
    ids, masters = _masters(1)
    rec = cosign(ids[0], create_revocation([Identity.generate().did]))
    assert verify_revocation({**rec, "record_id": "0" * 64}, masters) is None


def test_wrong_type_is_rejected():
    ids, masters = _masters(1)
    rec = cosign(ids[0], create_revocation([Identity.generate().did]))
    assert verify_revocation({**rec, "type": "secdogie/capability/v1"}, masters) is None


def test_revoked_list_shape():
    with pytest.raises(ValueError):
        create_revocation([])                                # at least one
    with pytest.raises(ValueError):
        create_revocation(["not-a-did"])                     # well-formed DIDs only
    with pytest.raises(ValueError):
        create_revocation([Identity.generate().did for _ in range(MAX_REVOKED + 1)])


def test_exclude_drops_a_signer_from_the_count():
    ids, masters = _masters(2, threshold=1)
    target = Identity.generate().did
    rec = cosign(ids[0], create_revocation([target]))
    assert verify_revocation(rec, masters) is not None
    assert verify_revocation(rec, masters, exclude={ids[0].did}) is None


def test_master_set_parsing(tmp_path):
    ids = [Identity.generate() for _ in range(3)]

    def write(body):
        p = tmp_path / "masters"
        p.write_text(body, encoding="utf-8")
        return p

    ok = write("# masters\n" + "".join(f"master_did = {i.did}\n" for i in ids) + "threshold = 2\n")
    ms = MasterSet.load(ok)
    assert len(ms) == 3 and ms.threshold == 2

    # default threshold is 1
    one = write(f"master_did = {ids[0].did}\n")
    assert MasterSet.load(one).threshold == 1

    for bad in [
        "".join(f"master_did = {i.did}\n" for i in ids) + "threshold = 4\n",   # k > n
        f"master_did = {ids[0].did}\nthreshold = 0\n",                          # k < 1
        f"master_did = {ids[0].did}\nmaster_did = {ids[0].did}\n",              # duplicate
        "unknown = x\n",                                                        # unknown key
        "master_did = not-a-did\n",                                            # malformed DID
        "threshold = 1\n",                                                      # no masters
    ]:
        with pytest.raises(ValueError):
            MasterSet.load(write(bad))
