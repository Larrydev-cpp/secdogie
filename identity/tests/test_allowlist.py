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


# ---- live enrollment ----------------------------------------------------------------


def test_append_authorized_is_durable_and_idempotent(tmp_path):
    from secdogie_identity import append_authorized

    p = tmp_path / "apps.allow"
    p.write_text(f"authorized_did = {Identity.generate().did}", encoding="utf-8")  # no trailing newline
    did = Identity.generate().did
    assert append_authorized(p, did, label="paired via webrtc 2026-10-07T12:00:00Z")
    assert not append_authorized(p, did, label="again")
    text = p.read_text(encoding="utf-8")
    assert text.count(did) == 1 and text.endswith("\n")
    assert text.splitlines()[1] == f"authorized_did = {did}  # paired via webrtc 2026-10-07T12:00:00Z"
    assert Allowlist.load(p).contains(did) and len(Allowlist.load(p)) == 2
    fresh = tmp_path / "new.allow"
    assert append_authorized(fresh, did) and Allowlist.load(fresh).dids() == {did}
    assert (fresh.stat().st_mode & 0o777) == 0o600


def test_append_authorized_refuses_bad_input(tmp_path):
    from secdogie_identity import append_authorized

    p = tmp_path / "apps.allow"
    with pytest.raises(ValueError):
        append_authorized(p, "did:key:nope")
    with pytest.raises(ValueError):
        append_authorized(p, Identity.generate().did, label="two\nlines")
    assert not p.exists()


def test_watcher_follows_the_file(tmp_path):
    from secdogie_identity import AllowlistWatcher, append_authorized

    a, b = Identity.generate().did, Identity.generate().did
    p = tmp_path / "apps.allow"
    p.write_text(f"authorized_did = {a}\n", encoding="utf-8")
    live = Allowlist.load(p)
    seen = []
    w = AllowlistWatcher(p, live, on_change=lambda added, removed: seen.append((added, removed)))
    assert w.refresh() is None  # unchanged
    append_authorized(p, b)
    assert w.refresh() == ({b}, set()) and live.contains(b)
    p.write_text(f"authorized_did = {b}\nbroken line\n", encoding="utf-8")
    assert w.refresh() is None and live.contains(a)  # a broken file changes nothing
    p.write_text(f"authorized_did = {b}\n", encoding="utf-8")
    assert w.refresh() == (set(), {a}) and not live.contains(a)
    assert seen == [({b}, set()), (set(), {a})]
