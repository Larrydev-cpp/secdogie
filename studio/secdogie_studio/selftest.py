"""``secdogie-studio --self-test``: the whole local loop, headless, in a
throwaway profile -- what CI runs on every OS after building a binary.

  1. create a profile (minimum key-derivation cost: the keys are discarded);
  2. start the node as a child and read its ready line;
  3. the ready DID is the profile's node DID;
  4. one dialogue round trip as Studio's App (``link.check_link``);
  5. close the node's standard input: it exits 0 and is gone.

Each step prints one JSON line; the exit code is 0 only if every step passed.
"""
from __future__ import annotations

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
    _emit(out, self_test="passed")
    return 0
