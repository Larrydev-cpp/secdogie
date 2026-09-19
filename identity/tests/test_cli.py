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
