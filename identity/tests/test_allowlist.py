from __future__ import annotations

import pytest
from secdogie_identity import Allowlist, Identity


def _write(tmp_path, text):
    p = tmp_path / "authorized.conf"
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_with_comments_labels_and_blanks(tmp_path):
    a, b = Identity.generate(), Identity.generate()
    p = _write(
        tmp_path,
        f"""
# authorized nodes for this mesh
authorized_did = {a.did}   win11-vm-2
authorized_did = {b.did}

""",
    )
    allow = Allowlist.load(p)
    assert len(allow) == 2
    assert allow.contains(a.did)
    assert allow.contains(b.did)
    assert not allow.contains(Identity.generate().did)


def test_unknown_key_rejected(tmp_path):
    p = _write(tmp_path, "peer = did:key:z6Mkabc\n")
    with pytest.raises(ValueError):
        Allowlist.load(p)


def test_malformed_line_rejected(tmp_path):
    p = _write(tmp_path, "authorized_did\n")
    with pytest.raises(ValueError):
        Allowlist.load(p)


def test_invalid_did_rejected(tmp_path):
    p = _write(tmp_path, "authorized_did = did:example:not-ed25519\n")
    with pytest.raises(ValueError):
        Allowlist.load(p)
