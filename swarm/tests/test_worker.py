"""The reference node executor runs a real crawl (via a fake opener) and signs
a result the coordinator accepts. Skips where the native route adapter / agent
are not available; CI builds the adapter so it runs."""

from __future__ import annotations

import pytest
from secdogie_identity import Allowlist, Identity
from secdogie_swarm import sign_task, verify_result
from secdogie_swarm.tasks import build_crawl


def _worker_ready() -> bool:
    try:
        import secdogie_agent.browse  # noqa: F401
        import secdogie_graph

        secdogie_graph.library_path()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _worker_ready(), reason="agent.browse / native route adapter not available")


class FakeResp:
    def __init__(self, status, headers, body):
        self.status = status
        self._h = {k.lower(): v for k, v in headers.items()}
        self._b = body

    def header(self, name):
        return self._h.get(name.lower())

    def read_capped(self, maxb):
        return (self._b[:maxb], len(self._b) > maxb)


def fake_opener(responses):
    def opener(url, headers, timeout_s):
        assert "Cookie" not in headers and "Authorization" not in headers
        if url not in responses:
            raise ConnectionError("refused")
        return responses[url]

    return opener


def test_run_crawl_signs_a_result_the_core_accepts() -> None:
    from secdogie_swarm.worker import run_crawl

    core, node = Identity.generate(), Identity.generate()
    task = build_crawl(
        task_id="t-1",
        url="https://docs.example.com/",
        allowed_origins=["https://docs.example.com"],
        issued_at=0.0,
        deadline_ms=10000,
        redundancy=3,
        epoch=1,
    )
    signed = sign_task(core, task)
    html = b'<a href="/account/delete">x</a><a href="https://evil.test/y">y</a>'
    opener = fake_opener({"https://docs.example.com/": FakeResp(200, {"content-type": "text/html"}, html)})

    result = run_crawl(signed, node, now=1.0, opener=opener, core_allow=Allowlist({core.did}))

    out = result["output"]
    assert out["document"]["origin"] == "https://docs.example.com"
    cands = out["candidates"]
    assert len(cands) == 1 and cands[0]["route"] == "/account/delete"  # the on-origin link only
    assert all(c["origin"] == "https://docs.example.com" for c in cands)
    ok, reason, signer, _digest = verify_result(
        result, task_id="t-1", assigned={node.did}, allowlist=Allowlist({node.did})
    )
    assert ok and signer == node.did


def test_worker_refuses_a_task_not_from_a_trusted_core() -> None:
    from secdogie_swarm.worker import run_crawl

    core, stranger, node = Identity.generate(), Identity.generate(), Identity.generate()
    task = build_crawl(
        task_id="t-2",
        url="https://docs.example.com/",
        allowed_origins=["https://docs.example.com"],
        issued_at=0.0,
        deadline_ms=10000,
        redundancy=1,
        epoch=1,
    )
    signed = sign_task(core, task)
    with pytest.raises(ValueError):
        run_crawl(signed, node, now=1.0, core_allow=Allowlist({stranger.did}))
