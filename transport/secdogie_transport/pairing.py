"""Pairing one of your own devices into your mesh, over WebRTC.

The mesh is made of your own devices. A new one joins like this:

  1. A device already in the mesh makes an **invite**: a short-lived signed
     statement with the mesh's name, the inviter's DID, a one-time room at the
     signaling gateway, and where that gateway is. It travels as a link.
  2. On the new device the link opens a page. The person reads who invites
     and clicks **agree**. Nothing connects before that click.
  3. The two devices open a WebRTC data channel through the room. Each then
     sends a **bind**: its DID, a nonce, and the DTLS fingerprints it sees
     (its own and the other side's), signed. If someone sat in the middle of
     the signaling, the fingerprints do not match and pairing stops.
  4. Both screens show the same six-digit **check code**, derived from both
     binds. The person compares them and confirms on the inviting device. An
     invite link that leaked cannot add a stranger's device, because the code
     on the device in your hand would differ.
  5. The inviter signs an **admission**: this DID is a device of this mesh.
     The new device keeps it, and any node can verify it against the mesh's
     owners.

The signaling gateway only introduces the two devices. It never sees a key or
the data channel's content, and it cannot add a device: every statement here
is signed, and the fingerprints bind the channel to the signers.

Every field is a string or an integer (never a float), so the canonical JSON
is the same bytes in Python and in the browser. ``webrtc/client/pairing.js``
mirrors this module; ``fixtures/vectors/pairing.json`` locks the two together.

Pure and deterministic; reuses the identity signing helpers (no new crypto).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass

from secdogie_identity import Allowlist, Identity, pubkey_from_did, sign_payload, verify_payload
from secdogie_identity.signing import canonical

INVITE_TYPE = "secdogie/device-invite/v1"
BIND_TYPE = "secdogie/carrier-bind/v1"
SAS_TYPE = "secdogie/pair-sas/v1"
ADMISSION_TYPE = "secdogie/device-admission/v1"

INVITER, DEVICE = "inviter", "device"
DEFAULT_TTL = 600  # an invite lives ten minutes
MAX_TTL = 3600
MAX_SKEW = 120  # seconds a bind's clock may differ from ours
MAX_NAME = 40
ROLES = ("forward", "serve", "model", "relay", "ingest")

_ROOM = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_FP = re.compile(r"^[0-9a-f]{64}$")
_SIGNAL = re.compile(r"^wss?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._~/\-]*)?$")


@dataclass(frozen=True)
class Checked:
    ok: bool
    reason: str | None = None
    value: dict | None = None


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _name(name) -> str:
    text = "".join(c for c in str(name or "") if c.isprintable()).strip()
    return text[:MAX_NAME]


def normalize_fingerprint(text) -> str:
    """A DTLS certificate fingerprint as 64 lower-case hex digits. SDP writes it
    as ``sha-256 AB:CD:...``; anything that is not a SHA-256 fingerprint is ''."""
    if not isinstance(text, str):
        return ""
    t = text.strip().lower()
    if t.startswith("sha-256"):
        t = t[len("sha-256"):].strip()
    t = t.replace(":", "")
    return t if _FP.match(t) else ""


# ---- the invite ---------------------------------------------------------------------


def create_invite(inviter: Identity, mesh: str, *, room: str, signal: str, ttl: int = DEFAULT_TTL,
                  clock=time.time) -> dict:
    """Sign an invite to ``mesh`` for one device, valid for ``ttl`` seconds."""
    if not isinstance(mesh, str) or not mesh or len(mesh) > 64:
        raise ValueError("an invite names its mesh")
    if not isinstance(room, str) or not _ROOM.match(room):
        raise ValueError("the room is 1-64 letters, digits, - or _")
    if not isinstance(signal, str) or not _SIGNAL.match(signal):
        raise ValueError("the signaling address is a ws:// or wss:// URL")
    if not _int(ttl) or not 0 < ttl <= MAX_TTL:
        raise ValueError(f"an invite lives between 1 and {MAX_TTL} seconds")
    body = {"type": INVITE_TYPE, "mesh": mesh, "inviter": inviter.did, "room": room, "signal": signal,
            "expires_at": int(clock()) + ttl}
    body["invite_id"] = hashlib.sha256(canonical(body)).hexdigest()[:32]
    return sign_payload(inviter, body)


def encode_invite(signed: dict) -> str:
    """The invite as it travels in a link: base64url of its canonical JSON."""
    return base64.urlsafe_b64encode(canonical(signed)).decode("ascii").rstrip("=")


def decode_invite(text: str) -> dict | None:
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def verify_invite(obj, *, now: float | None = None, clock=time.time) -> Checked:
    """An invite verifies when it is well formed, signed by the inviter it
    names, carries the id of its own content, and has not expired."""
    if not isinstance(obj, dict) or obj.get("type") != INVITE_TYPE:
        return Checked(False, "not an invite")
    ok, signer = verify_payload(obj, None)
    if not ok or obj.get("inviter") != signer:
        return Checked(False, "the signature is not the inviter's")
    mesh, room, signal, exp = obj.get("mesh"), obj.get("room"), obj.get("signal"), obj.get("expires_at")
    if not isinstance(mesh, str) or not mesh or len(mesh) > 64:
        return Checked(False, "no mesh name")
    if not isinstance(room, str) or not _ROOM.match(room) or not isinstance(signal, str) or not _SIGNAL.match(signal):
        return Checked(False, "bad room or signaling address")
    if not _int(exp):
        return Checked(False, "bad expiry")
    body = {k: obj[k] for k in ("type", "mesh", "inviter", "room", "signal", "expires_at")}
    if obj.get("invite_id") != hashlib.sha256(canonical(body)).hexdigest()[:32]:
        return Checked(False, "the invite id does not match its content")
    t = float(now) if now is not None else float(clock())
    if t >= exp:
        return Checked(False, "the invite has expired")
    return Checked(True, None, obj)


# ---- the bind: this channel belongs to these two keys --------------------------------


def create_bind(identity: Identity, *, invite_id: str, role: str, nonce: str, local_fp: str, remote_fp: str,
                name: str = "", clock=time.time) -> dict:
    """Sign what this side sees of the data channel: its own DTLS fingerprint
    and the other side's, with a fresh nonce."""
    local, remote = normalize_fingerprint(local_fp), normalize_fingerprint(remote_fp)
    if role not in (INVITER, DEVICE):
        raise ValueError("role is inviter or device")
    if not isinstance(invite_id, str) or not _HEX32.match(invite_id) or not isinstance(nonce, str) \
            or not _HEX32.match(nonce):
        raise ValueError("invite_id and nonce are 32 hex digits")
    if not local or not remote:
        raise ValueError("both fingerprints are SHA-256 fingerprints")
    body = {"type": BIND_TYPE, "invite_id": invite_id, "did": identity.did, "role": role, "nonce": nonce,
            "local_fp": local, "remote_fp": remote, "name": _name(name), "ts": int(clock())}
    return sign_payload(identity, body)


def verify_bind(obj, *, invite: dict, expect_role: str, my_local_fp: str, my_remote_fp: str,
                now: float | None = None, clock=time.time) -> Checked:
    """Check the other side's bind against what this side sees. The fingerprints
    must cross-match: its own is the one we see as remote, and the one it sees
    as remote is ours. A mismatch means someone is between the two devices."""
    if not isinstance(obj, dict) or obj.get("type") != BIND_TYPE:
        return Checked(False, "not a bind")
    ok, signer = verify_payload(obj, None)
    if not ok or obj.get("did") != signer:
        return Checked(False, "the signature is not the sender's")
    if obj.get("invite_id") != invite.get("invite_id"):
        return Checked(False, "the bind is for another invite")
    if obj.get("role") != expect_role:
        return Checked(False, "the wrong side sent this bind")
    if expect_role == INVITER and signer != invite.get("inviter"):
        return Checked(False, "the other side is not the inviter")
    if expect_role == DEVICE and signer == invite.get("inviter"):
        return Checked(False, "a device cannot pair with itself")
    nonce, ts = obj.get("nonce"), obj.get("ts")
    if not isinstance(nonce, str) or not _HEX32.match(nonce) or not _int(ts):
        return Checked(False, "bad nonce or time")
    if obj.get("name") != _name(obj.get("name")):
        return Checked(False, "bad device name")
    mine, theirs = normalize_fingerprint(my_local_fp), normalize_fingerprint(my_remote_fp)
    if not mine or not theirs:
        return Checked(False, "this side has no fingerprints yet")
    if obj.get("local_fp") != theirs or obj.get("remote_fp") != mine:
        return Checked(False, "the fingerprints do not match: someone is between the two devices")
    t = float(now) if now is not None else float(clock())
    if abs(t - ts) > MAX_SKEW:
        return Checked(False, "the bind is too old or from the future")
    return Checked(True, None, obj)


def check_code(invite_id: str, inviter_bind: dict, device_bind: dict) -> str:
    """The six digits both screens show: a hash of both binds' keys, nonces and
    fingerprints."""
    def part(b: dict) -> dict:
        return {"did": b["did"], "nonce": b["nonce"], "local_fp": b["local_fp"]}

    digest = hashlib.sha256(canonical({"type": SAS_TYPE, "invite_id": invite_id, "inviter": part(inviter_bind),
                                       "device": part(device_bind)})).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


# ---- the admission: this DID is a device of this mesh ---------------------------------


def create_admission(owner: Identity, mesh: str, device_did: str, *, invite_id: str, name: str = "",
                     roles=("forward",), clock=time.time) -> dict:
    """Sign that ``device_did`` is a device of ``mesh``, with these roles."""
    pubkey_from_did(device_did)
    if device_did == owner.did:
        raise ValueError("a device does not admit itself")
    roles = tuple(sorted(set(roles)))
    if not roles or any(r not in ROLES for r in roles):
        raise ValueError(f"roles are among {', '.join(ROLES)}")
    if not isinstance(invite_id, str) or not _HEX32.match(invite_id):
        raise ValueError("invite_id is 32 hex digits")
    body = {"type": ADMISSION_TYPE, "mesh": mesh, "device": device_did, "name": _name(name), "roles": list(roles),
            "invite_id": invite_id, "admitted_at": int(clock())}
    return sign_payload(owner, body)


def verify_admission(obj, *, owners, mesh: str | None = None, device: str | None = None) -> Checked:
    """An admission verifies when one of the mesh's ``owners`` signed it, for
    this mesh and (when given) this device."""
    if not isinstance(obj, dict) or obj.get("type") != ADMISSION_TYPE:
        return Checked(False, "not an admission")
    if owners is None:
        return Checked(False, "no owners configured")
    ok, signer = verify_payload(obj, owners)
    if not ok:
        return Checked(False, "not signed by an owner of this mesh" if signer else "invalid or missing signature")
    if mesh is not None and obj.get("mesh") != mesh:
        return Checked(False, "the admission is for another mesh")
    dev = obj.get("device")
    if not isinstance(dev, str) or dev == signer or (device is not None and dev != device):
        return Checked(False, "the admission is for another device")
    roles = obj.get("roles")
    if not isinstance(roles, list) or not roles or any(r not in ROLES for r in roles) or roles != sorted(set(roles)):
        return Checked(False, "bad roles")
    if not _int(obj.get("admitted_at")) or obj.get("name") != _name(obj.get("name")):
        return Checked(False, "bad admission")
    return Checked(True, None, obj)


def own_devices(admissions, *, owners, mesh: str) -> frozenset[str]:
    """The devices of ``mesh``: the device DIDs of the admissions that one of
    its ``owners`` signed. This is what makes a paired device one of *your*
    nodes, e.g. ``PeerBook(me.did, own=own_devices(...))``; an admission that
    does not verify is skipped."""
    allow = owners if isinstance(owners, Allowlist) else Allowlist(set(owners or ()))
    found = set()
    for obj in admissions or ():
        checked = verify_admission(obj, owners=allow, mesh=mesh)
        if checked.ok:
            found.add(checked.value["device"])
    return frozenset(found)


__all__ = ["ADMISSION_TYPE", "BIND_TYPE", "DEVICE", "INVITER", "INVITE_TYPE", "SAS_TYPE", "Checked", "check_code",
           "create_admission", "create_bind", "create_invite", "decode_invite", "encode_invite",
           "normalize_fingerprint", "own_devices", "verify_admission", "verify_bind", "verify_invite"]
