from __future__ import annotations

import json

from secdogie_identity import Identity
from secdogie_identity.cli import main


def test_genkey_writes_0600(tmp_path):
    key = tmp_path / "node.key"
    assert main(["genkey", str(key)]) == 0
    assert key.stat().st_mode & 0o777 == 0o600
    # The file round-trips into a working identity.
    idn = Identity.load(key)
    assert idn.did.startswith("did:key:z")


def test_did_command_prints_document(tmp_path, capsys):
    key = tmp_path / "node.key"
    main(["genkey", str(key)])
    capsys.readouterr()
    assert main(["did", str(key)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["id"] == Identity.load(key).did


def test_verify_authorized_and_not(tmp_path):
    key = tmp_path / "node.key"
    main(["genkey", str(key)])
    did = Identity.load(key).did

    allow = tmp_path / "authorized.conf"
    allow.write_text(f"authorized_did = {did}\n", encoding="utf-8")
    assert main(["verify", str(key), str(allow)]) == 0

    empty = tmp_path / "empty.conf"
    empty.write_text("# nobody\n", encoding="utf-8")
    assert main(["verify", str(key), str(empty)]) == 1


def _mk(tmp_path, name):
    key = tmp_path / name
    main(["genkey", str(key)])
    return key, Identity.load(key).did


def test_grant_output_verifies(tmp_path, capsys):
    op_key, op_did = _mk(tmp_path, "op.key")
    _node_key, node_did = _mk(tmp_path, "node.key")

    assert main(["grant", str(op_key), node_did, "--scope", "physical.click"]) == 0
    grant = json.loads(capsys.readouterr().out)
    assert grant["scopes"] == ["physical.click"]
    assert grant["issuer"] == op_did and grant["subject"] == node_did

    # the emitted grant verifies against an allowlist naming the issuer
    from secdogie_identity import Allowlist
    from secdogie_identity.capability import verify_capability
    res = verify_capability(grant, issuers=Allowlist({op_did}), subject=node_did)
    assert res.ok and res.scopes == ("physical.click",)


def test_grant_rejects_ungrantable_scope(tmp_path, capsys):
    op_key, _op_did = _mk(tmp_path, "op.key")
    _node_key, node_did = _mk(tmp_path, "node.key")
    assert main(["grant", str(op_key), node_did, "--scope", "process.memory.write"]) == 2
    assert "cannot grant" in capsys.readouterr().err


def test_verify_grant_paths(tmp_path, capsys):
    op_key, op_did = _mk(tmp_path, "op.key")
    _node_key, node_did = _mk(tmp_path, "node.key")
    main(["grant", str(op_key), node_did, "--scope", "physical.click"])
    grant_json = capsys.readouterr().out
    gpath = tmp_path / "g.json"
    gpath.write_text(grant_json, encoding="utf-8")

    trusted = tmp_path / "issuers.conf"
    trusted.write_text(f"authorized_did = {op_did}\n", encoding="utf-8")
    assert main(["verify-grant", str(gpath), str(trusted), "--subject", node_did]) == 0
    assert "valid:" in capsys.readouterr().out

    # an untrusted issuer allowlist rejects it
    _stranger_key, stranger_did = _mk(tmp_path, "stranger.key")
    untrusted = tmp_path / "untrusted.conf"
    untrusted.write_text(f"authorized_did = {stranger_did}\n", encoding="utf-8")
    assert main(["verify-grant", str(gpath), str(untrusted)]) == 1

    # right issuer but wrong expected subject
    assert main(["verify-grant", str(gpath), str(trusted), "--subject", stranger_did]) == 1


def test_revoke_propose_cosign_verify_roundtrip(tmp_path, capsys):
    m1_key, m1_did = _mk(tmp_path, "m1.key")
    m2_key, m2_did = _mk(tmp_path, "m2.key")
    _victim_key, victim_did = _mk(tmp_path, "victim.key")

    masters = tmp_path / "masters.conf"
    masters.write_text(
        f"master_did = {m1_did}\nmaster_did = {m2_did}\nthreshold = 2\n", encoding="utf-8"
    )
    rec = tmp_path / "rev.json"
    assert main(["revoke-propose", victim_did, "--reason", "test", "--out", str(rec)]) == 0

    # one signature: short of the 2-of-2 threshold
    assert main(["revoke-cosign", str(m1_key), str(rec)]) == 0
    assert main(["revoke-verify", str(rec), str(masters)]) == 1

    # second master signature reaches the threshold
    assert main(["revoke-cosign", str(m2_key), str(rec)]) == 0
    capsys.readouterr()
    assert main(["revoke-verify", str(rec), str(masters)]) == 0
    out = capsys.readouterr().out
    assert victim_did in out and "valid:" in out


def test_revoke_verify_rejects_a_non_master_signature(tmp_path, capsys):
    _m_key, m_did = _mk(tmp_path, "m.key")
    stranger_key, _stranger_did = _mk(tmp_path, "stranger.key")
    _victim_key, victim_did = _mk(tmp_path, "victim.key")

    masters = tmp_path / "masters.conf"
    masters.write_text(f"master_did = {m_did}\n", encoding="utf-8")
    rec = tmp_path / "rev.json"
    main(["revoke-propose", victim_did, "--out", str(rec)])
    main(["revoke-cosign", str(stranger_key), str(rec)])  # not a master
    assert main(["revoke-verify", str(rec), str(masters)]) == 1


def test_revoke_apply_appends_once_and_refuses_invalid(tmp_path, capsys):
    m_key, m_did = _mk(tmp_path, "m.key")
    stranger_key, _ = _mk(tmp_path, "stranger.key")
    _victim_key, victim_did = _mk(tmp_path, "victim.key")
    masters = tmp_path / "masters.conf"
    masters.write_text(f"master_did = {m_did}\n", encoding="utf-8")
    store = tmp_path / "revocations.jsonl"

    good = tmp_path / "good.json"
    main(["revoke-propose", victim_did, "--out", str(good)])
    main(["revoke-cosign", str(m_key), str(good)])
    assert main(["revoke-apply", str(good), "--masters", str(masters), "--store", str(store)]) == 0
    assert main(["revoke-apply", str(good), "--masters", str(masters), "--store", str(store)]) == 0
    assert len(store.read_text(encoding="utf-8").splitlines()) == 1  # not written twice

    bad = tmp_path / "bad.json"
    main(["revoke-propose", victim_did, "--reason", "forged", "--out", str(bad)])
    main(["revoke-cosign", str(stranger_key), str(bad)])
    assert main(["revoke-apply", str(bad), "--masters", str(masters), "--store", str(store)]) == 1
    assert len(store.read_text(encoding="utf-8").splitlines()) == 1  # store unchanged
