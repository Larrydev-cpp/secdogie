"""A decided crawl consensus is written into the real topology DAG: run a crawl
(via a fake opener), feed the output to the anchor, and see the state, the
observation and the reference appear in the store's view. Skips without the
native adapter; runs in runtime-tests."""

from __future__ import annotations

import pytest
from secdogie_identity import Allowlist, Identity
from secdogie_swarm import DagAnchor, sign_task
from secdogie_swarm.tasks import build_crawl


def _ready() -> bool:
    try:
        import secdogie_agent.browse  # noqa: F401
        import secdogie_graph  # noqa: F401

        secdogie_graph.library_path()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="agent.browse / native route adapter not available")


class FakeResp:
    def __init__(self, status, headers, body):
        self.status = status
        self._h = {k.lower(): v for k, v in headers.items()}
        self._b = body

    def header(self, name):
        return self._h.get(name.lower())

    def read_capped(self, maxb):
        return (self._b[:maxb], len(self._b) > maxb)


def test_a_decided_crawl_lands_in_the_dag() -> None:
    from secdogie_graph import DagStore
    from secdogie_swarm.worker import run_crawl

    core, node = Identity.generate(), Identity.generate()
    task = build_crawl(
        task_id="t-1",
        url="https://docs.example.com/",
        allowed_origins=["https://docs.example.com"],
        issued_at_ms=0,
        deadline_ms=10000,
        redundancy=1,
        epoch=1,
    )
    signed = sign_task(core, task)
    html = b'<a href="/account/delete">go</a>'

    def opener(url, headers, timeout_s):
        return FakeResp(200, {"content-type": "text/html"}, html)

    result = run_crawl(signed, node, now_ms=1, opener=opener, core_allow=Allowlist({core.did}))

    store = DagStore([core.did])
    anchor = DagAnchor(core, store)
    outcome = anchor.anchor_crawl(result["output"])
    assert outcome["outcome"] == "inserted"

    view = store.view()
    # the document state and the linked candidate state are both present,
    doc_key = result["output"]["document"]["key"]
    cand_key = result["output"]["candidates"][0]["key"]
    state_keys = {s["key"] if isinstance(s, dict) else s for s in view["states"]}
    assert doc_key in state_keys and cand_key in state_keys
    # and the reference from the document to the candidate was recorded.
    refs = view["references"]
    assert any(r.get("from") == doc_key and r.get("to") == cand_key for r in refs)
    store.close()


def test_anchor_skips_a_non_page() -> None:
    core = Identity.generate()

    class FakeStore:
        def heads(self):  # pragma: no cover - must not be called
            raise AssertionError("should not anchor a non-page")

        def ingest(self, wire):  # pragma: no cover
            raise AssertionError("should not anchor a non-page")

    anchor = DagAnchor(core, FakeStore())
    assert anchor.anchor_crawl({"kind": "refused", "document": None, "candidates": []}) is None
