"""Tests for the SecDogie Spiking Cortex Playground server API (/api/health, /api/deliberate, /api/approve)."""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from secdogie_citadel.mcp_guard import ApprovalStore

SERVER_PATH = Path(__file__).resolve().parents[2] / "docs" / "playground" / "server.py"


def _load_server_module():
    spec = importlib.util.spec_from_file_location("secdogie_playground_server", SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_playground_live_api_deliberate_and_approve():
    mod = _load_server_module()
    store = ApprovalStore()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mod.make_handler(store))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(f"{base}/api/health", timeout=5) as r:
            health = json.loads(r.read().decode("utf-8"))
        assert health["ok"] is True
        assert health["neurons"] == 1280
        assert health["synapses"] > 3_000_000

        req = urllib.request.Request(
            f"{base}/api/deliberate",
            data=json.dumps({
                "text": "客服刚打电话让我立刻把50000元转到安全账户验证",
                "tool_name": "bank_transfer",
                "irreversible": True,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read().decode("utf-8"))
        assert res["mode"] == "live"
        assert res["allow"] is False
        assert res["requires_human_approval"] is True
        assert res["snn"]["neurons"] == 1280
        assert res["snn"]["total_spikes"] > 0
        assert len(res["snn"]["raster"]) > 0
        assert len(res["socratic"]) >= 2

        ticket = res["approval"]
        assert ticket is not None and len(ticket["code"]) == 6

        approve_req = urllib.request.Request(
            f"{base}/api/approve",
            data=json.dumps({
                "ticket_id": ticket["ticket_id"],
                "approve": True,
                "code": ticket["code"],
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(approve_req, timeout=5) as r:
            approved = json.loads(r.read().decode("utf-8"))
        assert approved["status"] == "approved"
    finally:
        httpd.shutdown()
        httpd.server_close()
