"""The window's own node: what the first run creates (and with what
permissions), what the next run reuses, what the node trusts, and what it is
granted."""
from __future__ import annotations

import os
import stat
import sys
import threading
import time

import pytest

pytest.importorskip("nacl")

from nacl import pwhash  # noqa: E402
from secdogie_app.local import (  # noqa: E402
    DESKTOP_SCOPES,
    AlreadyRunning,
    ApiKeys,
    LocalBackend,
    OperatorKey,
    default_home,
    passphrase_problem,
)
from secdogie_dialogue.keystore import KeystoreError, seal_identity  # noqa: E402
from secdogie_identity import Allowlist, Identity  # noqa: E402

CHEAP = {"opslimit": pwhash.argon2id.OPSLIMIT_MIN, "memlimit": pwhash.argon2id.MEMLIMIT_MIN}
PASS = "correct horse"


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def backends(tmp_path):
    made = []

    def build(**kw):
        b = LocalBackend(tmp_path / "home", kdf=CHEAP, run_task=lambda *a, **k: (0, "ok"), **kw)
        made.append(b)
        return b

    yield build
    for b in made:
        try:
            b.stop()
        except Exception:  # noqa: BLE001 - already stopped by the test
            pass


def test_the_first_run_creates_keys_and_no_operator_key(backends, tmp_path):
    b = backends()
    home = tmp_path / "home"
    assert (home / "node.key").exists() and (home / "app.key").exists()
    assert not (home / "operator.keystore").exists() and not b.operator_key.is_set
    assert len(b.operators) == 0  # until the passphrase is set, nothing high-risk can be approved
    if os.name == "posix":
        assert _mode(home) == 0o700
        assert _mode(home / "node.key") == 0o600 and _mode(home / "app.key") == 0o600
    assert b.node_identity.did != b.app_identity.did
    b.stop()
    again = backends()
    assert again.node_identity.did == b.node_identity.did and again.app_identity.did == b.app_identity.did


def test_no_issuer_key_is_ever_written(backends, tmp_path):
    b = backends()
    b.start()
    seeds = [p.read_text(encoding="utf-8") for p in (tmp_path / "home").iterdir() if p.suffix == ".key"]
    assert len(seeds) == 2 and all(b._issuer.seed_b64 not in s for s in seeds)


def test_the_node_is_granted_the_desktop_scopes_and_nothing_else(backends):
    b = backends()
    b.start()
    scopes = b.node.supervisor.node_scopes()
    assert scopes == frozenset(DESKTOP_SCOPES)
    assert "process.run" not in scopes and not any(s.startswith("network.") for s in scopes)


def test_a_grant_from_a_previous_run_is_not_trusted(backends):
    b = backends()
    b.start()
    old_issuer = b._issuer.did
    b.stop()
    again = backends()
    assert not again.node.supervisor.issuers.contains(old_issuer)
    assert again.node.supervisor.node_scopes() == frozenset()  # until this run's own grant
    again.start()
    assert again.node.supervisor.node_scopes() == frozenset(DESKTOP_SCOPES)


def test_the_node_trusts_only_this_window_and_listens_only_on_loopback(backends):
    b = backends()
    ctl = b.start()
    assert b.node.address[0] == "127.0.0.1"
    assert b.node.apps.dids() == {b.app_identity.did}
    pkt = ctl.add_goal("tidy up", "g1")
    assert ctl.wait_for(lambda c: c.requests()[pkt.request_id].reply, 10) == "accepted: goal g1 queued"


def test_a_second_window_is_refused(backends):
    backends()
    with pytest.raises(AlreadyRunning):
        backends()


def test_the_lock_is_released_on_stop(backends):
    b = backends()
    b.start()
    b.stop()
    backends()  # no AlreadyRunning


def test_default_home(monkeypatch, tmp_path):
    monkeypatch.setenv("SECDOGIE_HOME", str(tmp_path / "x"))
    assert default_home() == tmp_path / "x"
    monkeypatch.delenv("SECDOGIE_HOME")
    if sys.platform not in ("win32", "darwin"):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        assert default_home() == tmp_path / "cfg" / "secdogie" / "app"


# ---- the operator key --------------------------------------------------------------------


def test_passphrase_problem():
    assert passphrase_problem(PASS, PASS) is None
    assert passphrase_problem("", "")
    assert passphrase_problem("1234567", "1234567")
    assert passphrase_problem("12345678", "12345678") is None
    assert passphrase_problem(PASS, PASS + " ")


def test_create_then_unlock(tmp_path):
    trusted = Allowlist()
    key = OperatorKey(tmp_path / "op.keystore", trusted, kdf=CHEAP)
    with pytest.raises(KeystoreError, match="还没有"):
        key.unlock(PASS)
    made = key.create(PASS, PASS)
    assert trusted.dids() == {made.did} == {key.did}
    assert key.unlock(PASS).did == made.did
    with pytest.raises(KeystoreError):
        key.unlock("wrong passphrase")
    with pytest.raises(KeystoreError, match="已经设置"):
        key.create(PASS, PASS)
    # the next window trusts the same key, from the keystore alone
    later = Allowlist()
    assert OperatorKey(tmp_path / "op.keystore", later, kdf=CHEAP).did == made.did and later.dids() == {made.did}


def test_a_mismatched_pair_sets_nothing(tmp_path):
    trusted = Allowlist()
    key = OperatorKey(tmp_path / "op.keystore", trusted, kdf=CHEAP)
    with pytest.raises(KeystoreError, match="不一致"):
        key.create(PASS, PASS + "!")
    assert not key.is_set and len(trusted) == 0 and not (tmp_path / "op.keystore").exists()


def test_a_keystore_swapped_in_while_running_is_refused(tmp_path):
    path = tmp_path / "op.keystore"
    key = OperatorKey(path, Allowlist(), kdf=CHEAP)
    key.create(PASS, PASS)
    path.unlink()
    seal_identity(Identity.generate(), PASS.encode(), path, **CHEAP)  # someone else's key, same passphrase
    with pytest.raises(KeystoreError, match="different operator"):
        key.unlock(PASS)


# ---- the model's API key ----------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    pytest.importorskip("secdogie_agent")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "SECDOGIE_MODEL", "SECDOGIE_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_api_keys_where_the_agent_reads_them(clean_env):
    from secdogie_agent import config

    keys = ApiKeys()
    assert not keys.configured()
    assert keys.problem("") and keys.problem("sk-1") and keys.problem("sk-ant-a b c d e")
    with pytest.raises(ValueError):
        keys.save("sk-1")
    with pytest.raises(ValueError, match="provider"):
        keys.save("0123456789abcdef", provider="nobody")
    path = keys.save("sk-or-v1-0123456789", model="")
    assert keys.configured()
    assert config.resolve().provider == "openrouter" and config.resolve().api_key == "sk-or-v1-0123456789"
    if os.name == "posix":
        assert _mode(path) == 0o600


def test_the_grant_is_renewed_while_the_window_is_open(backends):
    b = backends(grant_ttl=0.3)
    b.start()
    deadline = time.time() + 5
    while time.time() < deadline and len(b.node.supervisor.grants()) < 3:
        threading.Event().wait(0.05)
    assert len(b.node.supervisor.grants()) >= 3
    assert b.node.supervisor.node_scopes() == frozenset(DESKTOP_SCOPES)


def test_the_windows_own_session_stays_on_loopback(backends):
    b = backends()
    b.start()
    assert b._app.channel.address[0] == "127.0.0.1"
