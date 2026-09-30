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


def test_any_of_trusts_what_any_part_trusts_and_follows_revocation():
    from secdogie_identity import AnyOf, Identity, MasterSet, TrustPolicy, cosign, create_revocation

    master, app, peer, stranger = (Identity.generate() for _ in range(4))
    apps = Allowlist({app.did})
    mesh = TrustPolicy(Allowlist({peer.did}), masters=MasterSet([master.did]))
    both = AnyOf(apps, mesh)
    assert both.contains(app.did) and both.contains(peer.did) and not both.contains(stranger.did)
    assert peer.did in both and 7 not in both
    assert both.dids() == {app.did, peer.did}
    mesh.apply(cosign(master, create_revocation([peer.did])))
    assert not both.contains(peer.did)  # consulted live
    assert not AnyOf(Allowlist()) and AnyOf(Allowlist(), apps)
    with pytest.raises(ValueError):
        AnyOf(apps, None)
    with pytest.raises(ValueError):
        AnyOf()
