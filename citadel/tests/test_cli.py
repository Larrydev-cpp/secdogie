"""Operator CLI: capability grants reach the journal and turn on enforcement.

These exercise the real argparse entry points (`secdogie_citadel.cli.main`) on a
temporary journal, the same way the other CLIs are tested."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("nacl")

from secdogie_citadel.cli import main  # noqa: E402
from secdogie_citadel.journal import Journal  # noqa: E402
from secdogie_citadel.state import StateStore  # noqa: E402
from secdogie_identity import Identity  # noqa: E402
from secdogie_identity.capability import create_capability  # noqa: E402


def _keyfile(tmp_path, name):
    idn = Identity.generate()
    path = tmp_path / name
    idn.save(path)
    return path, idn


def _write_grant(tmp_path, name, grant):
    path = tmp_path / name
    path.write_text(json.dumps(grant), encoding="utf-8")
    return path


def test_add_grant_then_scopes(tmp_path, capsys):
    db = str(tmp_path / "j.db")
    node_key, node = _keyfile(tmp_path, "node.key")
    op = Identity.generate()

    grant = create_capability(op, node.did, ["physical.click"], ttl=3600)
    gpath = _write_grant(tmp_path, "g.json", grant)
    issuers = tmp_path / "issuers.conf"
    issuers.write_text(f"authorized_did = {op.did}\n", encoding="utf-8")

    assert main(["add-grant", db, str(gpath), "--identity", str(node_key),
                 "--issuers", str(issuers)]) == 0
    assert "added grant" in capsys.readouterr().out

    assert main(["scopes", db, "--identity", str(node_key), "--issuers", str(issuers)]) == 0
    assert "physical.click" in capsys.readouterr().out


def test_add_grant_refuses_untrusted(tmp_path, capsys):
    db = str(tmp_path / "j.db")
    node_key, node = _keyfile(tmp_path, "node.key")
    op = Identity.generate()
    rogue = Identity.generate()

    # signed by a rogue issuer, but the allowlist only trusts `op`
    grant = create_capability(rogue, node.did, ["physical.click"], ttl=3600)
    gpath = _write_grant(tmp_path, "g.json", grant)
    issuers = tmp_path / "issuers.conf"
    issuers.write_text(f"authorized_did = {op.did}\n", encoding="utf-8")

    assert main(["add-grant", db, str(gpath), "--identity", str(node_key),
                 "--issuers", str(issuers)]) == 1
    assert "refusing to add grant" in capsys.readouterr().out

    # nothing was written to the journal
    events = [e for e in Journal(db).events() if e["kind"] == "capability_grant"]
    assert events == []


def test_run_issuers_gates_the_task(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "j.db")
    node_key, node = _keyfile(tmp_path, "node.key")
    op = Identity.generate()

    # a granted click, no type
    grant = create_capability(op, node.did, ["physical.click"], ttl=3600)
    gpath = _write_grant(tmp_path, "g.json", grant)
    issuers = tmp_path / "issuers.conf"
    issuers.write_text(f"authorized_did = {op.did}\n", encoding="utf-8")
    main(["add-grant", db, str(gpath), "--identity", str(node_key), "--issuers", str(issuers)])
    capsys.readouterr()

    seen = {}

    def fake_run_task(task, *, should_stop, on_status, confirm, record_step=None,
                      plan_gate=None, recovery=None):
        seen["click"] = plan_gate({"kind": "left_click", "x": 1, "y": 1}, [])
        seen["type"] = plan_gate({"kind": "type", "text": "hi"}, [])
        return (0, "ok")

    import secdogie_citadel.supervisor as sup_mod
    monkeypatch.setattr(sup_mod, "agent_run_task", fake_run_task)

    # a ready goal to run
    main(["add-goal", db, "g1", "--identity", str(node_key), "--title", "g1"])
    capsys.readouterr()

    assert main(["run", db, "--identity", str(node_key), "--issuers", str(issuers)]) == 0
    out = capsys.readouterr().out
    assert "capability enforcement on: physical.click" in out
    assert seen["click"][0] is True
    assert seen["type"][0] is False

    # the run was recorded in the journal state
    store = StateStore()
    store.merge_events(Journal(db).events())
    assert store.entities("run")


# --- learning chain: learn / knowledge / evidence-cat ------------------------


def test_knowledge_and_evidence_cat_without_desktop(tmp_path, capsys):
    # knowledge + evidence-cat need no browser; seed evidence directly.
    from secdogie_citadel.evidence import EvidenceStore, LocalBackend, record_knowledge
    from secdogie_citadel.journal import Journal

    node_key, node = _keyfile(tmp_path, "node.key")
    db = str(tmp_path / "j.db")
    ev = str(tmp_path / "evidence")
    doc = {"url": "https://example.com/", "title": "Example", "text": "hello", "ax": []}
    store = EvidenceStore(LocalBackend(ev))
    root = store.put_json(doc)
    j = Journal(db, identity=node)
    record_knowledge(j, root=root, url=doc["url"], title=doc["title"], size=42, preview="hello")
    j.close()

    assert main(["knowledge", db]) == 0
    out = capsys.readouterr().out
    assert root[:16] in out and "Example" in out

    assert main(["evidence-cat", root, "--evidence", ev]) == 0
    import json as _json
    assert _json.loads(capsys.readouterr().out) == doc

    # a missing root reports incompleteness, non-zero
    assert main(["evidence-cat", "a" * 64, "--evidence", ev]) == 1


def test_learn_reads_a_page_and_records_it(tmp_path, capsys, monkeypatch):
    websession = pytest.importorskip("secdogie_desktop.websession")

    node_key, _node = _keyfile(tmp_path, "node.key")
    db = str(tmp_path / "j.db")
    ev = str(tmp_path / "evidence")
    ss = tmp_path / "state.json"
    ss.write_text("{}", encoding="utf-8")

    class FakeDriver:
        def fetch(self, cfg, url):
            return websession.RawPage(
                url=url, title="Example Domain",
                ax_snapshot={"role": "WebArea", "name": "Example Domain",
                             "children": [{"role": "link", "name": "More information"}]},
                text="Example Domain. Illustrative examples.")

    monkeypatch.setattr(websession, "PlaywrightDriver", lambda *a, **k: FakeDriver())

    assert main(["learn", db, "http://example.com/", "--identity", str(node_key),
                 "--evidence", ev, "--storage-state", str(ss)]) == 0
    out = capsys.readouterr().out
    assert "learned http://example.com/" in out
    root = [ln for ln in out.splitlines() if "evidence:" in ln][0].split()[1]

    # the page is stored content-addressed and reads back with both senses
    import json as _json

    from secdogie_citadel.evidence import EvidenceStore, LocalBackend
    doc = _json.loads(EvidenceStore(LocalBackend(ev)).read_bytes(root))
    assert doc["title"] == "Example Domain" and "Illustrative" in doc["text"]
    assert doc["ax"][0]["children"][0]["name"] == "More information"

    # a non-http URL never drives the browser
    assert main(["learn", db, "file:///etc/passwd", "--identity", str(node_key),
                 "--evidence", ev, "--storage-state", str(ss)]) == 2
