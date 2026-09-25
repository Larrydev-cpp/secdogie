"""Encrypted direct frames (v2): confidentiality and replay protection for the
direct UDP data plane.

A v1 frame (udp.py) is DID-signed plaintext: authentic, but readable on the wire
and replayable. A v2 frame keeps the same signed envelope and seals the content
with PyNaCl's `Box` (X25519 + XSalsa20-Poly1305), keyed by:

  * this node's X25519 transport private key -- the same key file the C tunnel
    uses (`secdogie-tunnel genkey` writes `private_key = <base64>`), and
  * the peer's X25519 public key, taken ONLY from the peer's verified
    DID -> transport-key binding (Phase 2.1). Which key belongs to which DID is
    therefore a signed fact, never inferred from an address.

Wire format:

    outer (DID-signed): {t: "secdogie/direct/v2", from, to, ct: b64(Box ciphertext)}
    inner (plaintext):  json({from, to, ctr}) + b"\\n" + message bytes

The outer signature is checked (and the allowlist applied) before any decryption,
so junk is dropped cheaply. After decryption the inner from/to must match the
outer ones (a frame bounced back at its sender is rejected), and `ctr` goes
through a per-peer sliding `ReplayWindow`: a frame seen before, or too old, is
dropped. The sender's counter starts at the wall-clock nanosecond at startup and
increments per frame, so it keeps increasing across restarts.

Honest limits (by design, not oversights):

  * No forward secrecy. This is a static-static key agreement: whoever later
    obtains a node's private key can decrypt traffic recorded earlier. Where that
    matters, run over the C `tunnel/` or WireGuard, which do ephemeral handshakes.
  * Metadata is visible: who talks to whom, frame sizes and timing. There is no
    padding or traffic shaping (this project does no traffic obfuscation).
  * The X25519 key is shared with the tunnel. The two derive their session keys
    differently (NaCl's HSalsa20 here, the tunnel's own KDF there); a binding
    names one transport key per DID, rotated via its `key_version`.

No new primitives: PyNaCl's `Box` plus the existing sign/verify helpers.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from nacl.exceptions import CryptoError
from nacl.public import Box, PrivateKey, PublicKey
from secdogie_identity import Identity, sign_payload, verify_payload

SEALED_TYPE = "secdogie/direct/v2"
REPLAY_WINDOW = 1024
_KEY_LEN = 32


def private_key_from_b64(b64: str) -> PrivateKey:
    raw = base64.b64decode(b64.strip(), validate=True)
    if len(raw) != _KEY_LEN:
        raise ValueError(f"expected a {_KEY_LEN}-byte X25519 key, got {len(raw)} bytes")
    return PrivateKey(raw)


def public_key_from_b64(b64: str) -> PublicKey:
    raw = base64.b64decode(b64.strip(), validate=True)
    if len(raw) != _KEY_LEN:
        raise ValueError(f"expected a {_KEY_LEN}-byte X25519 key, got {len(raw)} bytes")
    return PublicKey(raw)


def public_key_b64(key: PrivateKey | PublicKey) -> str:
    pk = key.public_key if isinstance(key, PrivateKey) else key
    return base64.b64encode(bytes(pk)).decode("ascii")


def make_box(private: PrivateKey, peer_public: PublicKey) -> Box:
    """The shared-key box between this node's private key and a peer's public key."""
    return Box(private, peer_public)


def load_transport_key(path: str | os.PathLike) -> PrivateKey:
    """Read a node's X25519 private key from a tunnel key file
    (`private_key = <base64>`, as written by `secdogie-tunnel genkey`). Other
    `key = value` lines and `#` comments are ignored, so a full tunnel config
    file works too."""
    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        if key.strip() == "private_key":
            try:
                return private_key_from_b64(val)
            except ValueError as exc:
                raise ValueError(f"{path}:{lineno}: invalid private_key ({exc})") from exc
    raise ValueError(f"{path}: no private_key line")


class ReplayWindow:
    """A sliding window over a peer's frame counters (the WireGuard scheme): the
    highest counter seen plus a bitmap of the `size` counters below it. A counter
    is accepted once; anything at or beyond `size` behind the highest is too old."""

    def __init__(self, size: int = REPLAY_WINDOW):
        self.size = size
        self.highest: int | None = None
        self._bits = 0  # bit i set -> (highest - i) has been seen

    def accept(self, ctr: int) -> bool:
        """Record `ctr` and return True if it is new and inside the window; return
        False (recording nothing) for a duplicate or a counter that is too old.
        Call only for a frame that already decrypted and authenticated."""
        if self.highest is None:
            self.highest, self._bits = ctr, 1
            return True
        if ctr > self.highest:
            shift = ctr - self.highest
            self._bits = ((self._bits << shift) | 1) & ((1 << self.size) - 1) if shift < self.size else 1
            self.highest = ctr
            return True
        diff = self.highest - ctr
        if diff >= self.size or (self._bits >> diff) & 1:
            return False
        self._bits |= 1 << diff
        return True


def seal(identity: Identity, box: Box, to_did: str, ctr: int, message: bytes) -> bytes:
    """Build a signed v2 frame carrying `message` to `to_did`."""
    header = json.dumps({"from": identity.did, "to": to_did, "ctr": ctr}).encode("utf-8")
    ct = box.encrypt(header + b"\n" + message)  # a fresh random nonce is prepended
    envelope = {
        "t": SEALED_TYPE,
        "from": identity.did,
        "to": to_did,
        "ct": base64.b64encode(bytes(ct)).decode("ascii"),
    }
    return json.dumps(sign_payload(identity, envelope)).encode("utf-8")


def open_sealed(raw: bytes, *, allowlist, self_did: str, box_for) -> tuple[str, int, bytes] | None:
    """Verify and decrypt a v2 frame addressed to `self_did`. `box_for(did)` returns
    the `Box` for a peer with a verified binding, or None. Returns
    (signer_did, ctr, message), or None for anything that does not check out.
    Replay checking is the caller's (it keeps the per-peer windows)."""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict) or obj.get("t") != SEALED_TYPE:
        return None
    ok, signer = verify_payload(obj, allowlist)
    if not ok or obj.get("from") != signer or obj.get("to") != self_did:
        return None
    box = box_for(signer)
    if box is None:
        return None  # no verified key for this peer: cannot (and must not) accept
    try:
        plain = box.decrypt(base64.b64decode(obj["ct"], validate=True))
    except (KeyError, ValueError, TypeError, CryptoError):
        return None
    header_raw, sep, message = plain.partition(b"\n")
    if not sep:
        return None
    try:
        header = json.loads(header_raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(header, dict) or header.get("from") != signer or header.get("to") != self_did:
        return None
    ctr = header.get("ctr")
    if not isinstance(ctr, int) or isinstance(ctr, bool) or ctr < 0:
        return None
    return signer, ctr, message


__all__ = [
    "SEALED_TYPE",
    "REPLAY_WINDOW",
    "ReplayWindow",
    "load_transport_key",
    "make_box",
    "private_key_from_b64",
    "public_key_from_b64",
    "public_key_b64",
    "seal",
    "open_sealed",
]
