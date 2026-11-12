"""``secdogie-studio --self-test``: the whole local loop, headless, in a
throwaway profile -- what CI runs on every OS after building a binary.

  1. create a profile (minimum key-derivation cost: the keys are discarded);
  2. start the node as a child and read its ready line;
  3. the ready DID is the profile's node DID;
  4. one dialogue round trip as Studio's App (``link.check_link``);
  5. close the node's standard input: it exits 0 and is gone;
  6. WebRTC on this machine: a signaling gateway on loopback and two peers
     that open a real data channel and see the same DTLS fingerprints (what
     pairing and "My devices" stand on).

Each step prints one JSON line; the exit code is 0 only if every step passed.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
import tempfile
from pathlib import Path

from .link import check_link
from .nodeproc import NodeError, NodeProcess
from .profile import create_profile


def _emit(out, **fields) -> None:
    out.write(json.dumps(fields, sort_keys=True) + "\n")
    out.flush()


def run_self_test(*, out=None, timeout: float = 30.0) -> int:
    out = out or sys.stdout
    with tempfile.TemporaryDirectory(prefix="secdogie-studio-selftest-") as tmp:
        profile = create_profile(Path(tmp) / "profile", secrets.token_hex(16).encode(), kdf="min")
        _emit(out, step="profile", ok=True, node=profile.node_did(), operator=profile.operator_did())
        node = NodeProcess(profile.node_args())
        node.start()
        try:
            try:
                ready = node.wait_ready(timeout)
            except NodeError as e:
                _emit(out, step="node", ok=False, error=str(e))
                return 1
            same = ready.did == profile.node_did()
            _emit(out, step="node", ok=same, did=ready.did, listen=f"{ready.host}:{ready.port}", pid=node.pid)
            if not same:
                return 1
            try:
                reply = check_link(profile, ready, timeout=timeout)
            except Exception as e:  # noqa: BLE001 - reported, not raised: this is a self-test
                _emit(out, step="link", ok=False, error=f"{type(e).__name__}: {e}")
                return 1
            ok = reply.startswith("refused: no active memory")
            _emit(out, step="link", ok=ok, reply=reply)
            if not ok:
                return 1
        finally:
            code = node.stop()
        stopped = code == 0 and not node.running
        _emit(out, step="stop", ok=stopped, returncode=code)
        if not stopped:
            return 1
    try:
        fps = asyncio.run(asyncio.wait_for(_webrtc_loopback(), timeout))
    except Exception as e:  # noqa: BLE001 - reported, not raised
        _emit(out, step="webrtc", ok=False, error=f"{type(e).__name__}: {e}")
        return 1
    _emit(out, step="webrtc", ok=True, fingerprint=fps[0][:16])
    _emit(out, self_test="passed")
    return 0


async def _webrtc_loopback() -> tuple[str, str]:
    from secdogie_transport.rtc import RtcPeer
    from secdogie_transport.signal_server import serve_signaling

    server = await serve_signaling("127.0.0.1", 0)
    a, b = RtcPeer(server.url, "self-test", ice_servers=()), RtcPeer(server.url, "self-test", ice_servers=())
    try:
        first = asyncio.create_task(a.open(20))
        await asyncio.sleep(0.2)
        await b.open(20)
        await first
        await b.send("ping")
        if await a.recv(5) != "ping":
            raise RuntimeError("the message did not arrive")
        la, ra = a.fingerprints()
        lb, rb = b.fingerprints()
        if not la or (la, ra) != (rb, lb):
            raise RuntimeError("the two ends see different connections")
        return la, ra
    finally:
        await a.close(linger=0)
        await b.close(linger=0)
        await server.close()
