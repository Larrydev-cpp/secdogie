"""Your own devices as one mesh: where two of them meet, how each end of a link
proves itself, and the roster of who belongs (docs/PLAN-OWN-DEVICES.zh.md).

  room      each pair of devices meets in its own room on the signaling
            gateway, derived from the mesh and the two DIDs, so both ends can
            work it out and the gateway's rooms of two fit exactly
  hello     once the WebRTC data channel is open, each side signs who it is
            and the DTLS certificate fingerprints it sees (as the pairing bind
            does), plus its admission; a mismatch means someone is between
            the two devices
  roster    the owners of the mesh and every admission they signed; devices
            trade it over their links, and only an owner's signature adds a
            device

Who may be on a link: an owner, or a device holding an admission an owner
signed for this mesh. Anyone else, however they reached the room, is refused
at the hello. Pure functions and a small class: no sockets, no clock unless
given one. webrtc/client/meshlink.js mirrors this byte for byte
(fixtures/vectors/meshlink.json).
"""
from __future__ import annotations

import hashlib
import re
import time

from secdogie_identity import Allowlist, sign_payload, verify_payload
from secdogie_identity.signing import canonical

from .pairing import ADMISSION_TYPE, MAX_NAME, Checked, normalize_fingerprint, verify_admission

ROOM_DOMAIN = "secdogie/link-room/v1"
HELLO_TYPE = "secdogie/link-hello/v1"
MESH_CHANNEL = "mesh/v1"  # the ChannelMux channel for roster, presence and notes
MAX_SKEW = 120  # seconds a hello's clock may differ from ours

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_DIGEST = re.compile(r"^([0-9a-f]{16})?$")


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _name(name) -> str:
    text = "".join(c for c in str(name or "") if c.isprintable()).strip()
    return text[:MAX_NAME]


# ---- where two devices meet -----------------------------------------------------------


def link_room(mesh: str, a: str, b: str) -> str:
    """The signaling room for the link between devices ``a`` and ``b`` of
    ``mesh``: the same whichever end asks, different for every pair."""
    if not isinstance(mesh, str) or not mesh or not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        raise ValueError("a link room needs a mesh and two DIDs")
    if a == b:
        raise ValueError("a device has no link to itself")
    lo, hi = sorted((a, b))
    return "l-" + hashlib.sha256(f"{ROOM_DOMAIN}|{mesh}|{lo}|{hi}".encode()).hexdigest()[:40]


# ---- who belongs ----------------------------------------------------------------------


class Roster:
    """The owners of ``mesh`` and the admissions they signed. Owners are local
    trust (yours, from configuration or from your own admission's signer);
    admissions are learned from anyone, and kept only when an owner signed them."""

    def __init__(self, mesh: str, owners):
        if not isinstance(mesh, str) or not mesh:
            raise ValueError("a roster names its mesh")
        self.mesh = mesh
        self.owners = frozenset(owners)
        if not self.owners:
            raise ValueError("a roster needs at least one owner")
        self._admissions: dict[str, dict] = {}

    def admit(self, admission) -> bool:
        """Keep an admission an owner signed for this mesh; True when it added
        or refreshed a device."""
        checked = verify_admission(admission, owners=Allowlist(set(self.owners)), mesh=self.mesh)
        if not checked.ok:
            return False
        device = checked.value["device"]
        held = self._admissions.get(device)
        if held is not None and held["admitted_at"] >= checked.value["admitted_at"]:
            return False
        self._admissions[device] = checked.value
        return True

    def is_member(self, did: str) -> bool:
        return did in self.owners or did in self._admissions

    def devices(self) -> list[str]:
        """Every device of the mesh, owners included, in a stable order."""
        return sorted(self.owners | set(self._admissions))

    def admission(self, did: str) -> dict | None:
        return self._admissions.get(did)

    def name(self, did: str) -> str:
        held = self._admissions.get(did)
        return held.get("name", "") if held else ""

    def wire(self) -> dict:
        """What a device hands its neighbours: the admissions. Owners never
        travel: each device trusts its own."""
        return {"mesh": self.mesh, "admissions": [self._admissions[d] for d in sorted(self._admissions)]}

    def merge(self, wire) -> int:
        """Take a neighbour's roster; returns how many devices it added or refreshed."""
        if not isinstance(wire, dict) or wire.get("mesh") != self.mesh or not isinstance(wire.get("admissions"), list):
            return 0
        return sum(1 for a in wire["admissions"][:256] if isinstance(a, dict) and self.admit(a))

    def digest(self) -> str:
        """16 hex digits naming what this roster holds, so two devices can tell
        whether they need to trade it."""
        body = {"mesh": self.mesh, "owners": sorted(self.owners),
                "admissions": sorted(a["sig"] for a in self._admissions.values())}
        return hashlib.sha256(canonical(body)).hexdigest()[:16]


# ---- each end of a link -----------------------------------------------------------------


def create_hello(identity, *, mesh: str, nonce: str, local_fp: str, remote_fp: str, admission=None,
                 name: str = "", roster: str = "", clock=time.time) -> dict:
    """Sign who this device is and what it sees of this connection."""
    local, remote = normalize_fingerprint(local_fp), normalize_fingerprint(remote_fp)
    if not isinstance(nonce, str) or not _HEX32.match(nonce):
        raise ValueError("the nonce is 32 hex digits")
    if not local or not remote:
        raise ValueError("both fingerprints are SHA-256 fingerprints")
    if admission is not None and (not isinstance(admission, dict) or admission.get("type") != ADMISSION_TYPE):
        raise ValueError("admission is a signed device admission, or None for an owner")
    if not isinstance(roster, str) or not _DIGEST.match(roster):
        raise ValueError("roster is a roster digest, or empty")
    body = {"type": HELLO_TYPE, "mesh": mesh, "did": identity.did, "nonce": nonce, "local_fp": local,
            "remote_fp": remote, "name": _name(name), "roster": roster, "admission": admission, "ts": int(clock())}
    return sign_payload(identity, body)


def verify_hello(obj, *, roster: Roster, expect_did: str, my_local_fp: str, my_remote_fp: str,
                 now: float | None = None, clock=time.time) -> Checked:
    """Check the other end of a link. ``expect_did`` is the device this link is
    for (its room was derived from it). The fingerprints must cross-match, as
    in pairing: a mismatch means someone is between the two devices."""
    if not isinstance(obj, dict) or obj.get("type") != HELLO_TYPE:
        return Checked(False, "not a hello")
    ok, signer = verify_payload(obj, Allowlist({expect_did}))
    if signer is None or obj.get("did") != signer:
        return Checked(False, "the signature is not the sender's")
    if not ok:
        return Checked(False, "this is not the device this link is for")
    if obj.get("mesh") != roster.mesh:
        return Checked(False, "the hello is for another mesh")
    admission = obj.get("admission")
    member = signer in roster.owners or roster.is_member(signer)
    if not member and isinstance(admission, dict):
        member = verify_admission(admission, owners=Allowlist(set(roster.owners)), mesh=roster.mesh,
                                  device=signer).ok
    if not member:
        return Checked(False, "not a device of this mesh")
    nonce, ts = obj.get("nonce"), obj.get("ts")
    if not isinstance(nonce, str) or not _HEX32.match(nonce) or not _int(ts) or obj.get("name") != _name(
            obj.get("name")) or not isinstance(obj.get("roster"), str) or not _DIGEST.match(obj["roster"]):
        return Checked(False, "bad hello")
    mine, theirs = normalize_fingerprint(my_local_fp), normalize_fingerprint(my_remote_fp)
    if not mine or not theirs:
        return Checked(False, "this side has no fingerprints yet")
    if obj.get("local_fp") != theirs or obj.get("remote_fp") != mine:
        return Checked(False, "the fingerprints do not match: someone is between the two devices")
    t = clock() if now is None else now
    if abs(t - ts) > MAX_SKEW:
        return Checked(False, "the hello is too old or from the future")
    return Checked(True, None, obj)


__all__ = ["HELLO_TYPE", "MAX_SKEW", "MESH_CHANNEL", "ROOM_DOMAIN", "Roster", "create_hello", "link_room",
           "verify_hello"]
