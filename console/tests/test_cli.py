"""Console command-line checks that run before anything is started."""
from __future__ import annotations

import pytest
from secdogie_console.cli import main


def test_revocation_flags_need_an_allowlist(capsys):
    assert main(["--no-browser", "--masters", "masters.conf"]) == 2
    assert "--masters" in capsys.readouterr().err


def test_revocations_need_masters(tmp_path, capsys):
    pytest.importorskip("nacl")
    from secdogie_identity import Identity

    allow = tmp_path / "operators.allow"
    allow.write_text(f"authorized_did = {Identity.generate().did}\n", encoding="utf-8")
    code = main(["--no-browser", "--operator-authorized", str(allow),
                 "--revocations", str(tmp_path / "r.jsonl")])
    assert code == 2
    assert "--masters" in capsys.readouterr().err
