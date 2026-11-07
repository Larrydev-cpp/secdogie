"""First run: three keys with three jobs, trust lists that name exactly them,
and nothing ever overwritten."""
from __future__ import annotations

import json
import os
import stat

import pytest
from secdogie_dialogue.keystore import KeystoreError, unseal_identity
from secdogie_identity import Allowlist
from secdogie_studio.profile import Profile, ProfileError, create_profile, default_root, parse_listen

PASS = b"correct horse battery"  # the conftest fixture's passphrase



def test_a_profile_has_three_distinct_keys_and_matching_trust_lists(profile):
    node, app, op = profile.node_did(), profile.app_did(), profile.operator_did()
    assert len({node, app, op}) == 3
    assert Allowlist.load(profile.path("apps.allow")).dids() == {app}
    assert Allowlist.load(profile.path("operators.allow")).dids() == {op}
    assert Allowlist.load(profile.path("nodes.allow")).dids() == {node}
    assert Allowlist.load(profile.path("issuers.allow")).dids() == {op}
    assert unseal_identity(profile.operator_keystore, PASS).did == op
    with pytest.raises(KeystoreError):
        unseal_identity(profile.operator_keystore, b"wrong passphrase")


def test_settings_are_safe_by_default(profile):
    s = profile.settings()
    assert s["roles"] == ["forward"] and s["listen"] == "127.0.0.1:0"
    assert s["mdns"] is False and s["keep_node_after_close"] is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_keys_and_the_directory_are_private(profile):
    for name in ("node.key", "app.key", "operator.keystore"):
        assert stat.S_IMODE(os.stat(profile.path(name)).st_mode) == 0o600, name
    assert stat.S_IMODE(os.stat(profile.root).st_mode) == 0o700


def test_the_node_runs_with_its_trust_sets_and_dies_with_its_parent(profile):
    args = profile.node_args()
    assert args[0] == "run" and "--exit-on-stdin-eof" in args
    for flag in ("--identity", "--apps", "--operators", "--authorized", "--issuers", "--journal"):
        assert flag in args
    assert "--insecure-dev" not in args


def test_never_overwrites_a_profile(profile):
    before = profile.node_key.read_bytes()
    with pytest.raises(ProfileError, match="refusing to overwrite"):
        create_profile(profile.root, PASS, kdf="min")
    assert profile.node_key.read_bytes() == before


def test_never_overwrites_keys_left_by_an_interrupted_first_run(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "node.key").write_text("left over", encoding="utf-8")
    with pytest.raises(ProfileError, match="node.key"):
        create_profile(root, PASS, kdf="min")
    assert (root / "node.key").read_text(encoding="utf-8") == "left over"
    assert not Profile(root).initialized


@pytest.mark.parametrize("kw, match", [
    ({"passphrase": b"short"}, "at least"),
    ({"roles": ()}, "roles"),
    ({"roles": ("forward", "forward")}, "roles"),
    ({"roles": ("admin",)}, "roles"),
    ({"listen": "nowhere"}, "HOST:PORT"),
    ({"kdf": "none"}, "key-derivation"),
])
def test_bad_first_run_input_is_refused_before_anything_is_written(tmp_path, kw, match):
    root = tmp_path / "p"
    passphrase = kw.pop("passphrase", PASS)
    with pytest.raises(ProfileError, match=match):
        create_profile(root, passphrase, **{"kdf": "min", **kw})
    assert not root.exists() or not any(root.iterdir())


def test_a_foreign_settings_file_is_not_a_profile(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "studio.json").write_text(json.dumps({"version": 99}), encoding="utf-8")
    with pytest.raises(ProfileError):
        Profile(root).settings()


def test_default_root_is_per_user_and_parse_listen():
    assert "secdogie-studio" in str(default_root())
    assert parse_listen("127.0.0.1:0") == ("127.0.0.1", 0)
    with pytest.raises(ProfileError):
        parse_listen(":7950")
