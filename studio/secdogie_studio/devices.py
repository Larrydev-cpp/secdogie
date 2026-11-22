"""Your own devices, in Studio: pairing and "My devices", over WebRTC.

Who is who:
  * the operator key owns the mesh. Writing an invitation is an authorization,
    so it asks for the passphrase; the key is unlocked for that one pairing
    (the invite, the bind, the admission) and dropped when it ends;
  * this machine is one of the devices: its node key, with an admission the
    operator signed for it (made at the first invitation);
  * or, when this Studio was invited into someone else's mesh, it is a member:
    its node key with the admission that owner signed, and it cannot invite.

What is kept, in the profile (mesh.json): the mesh's name, its owners, this
machine's own admission, the devices it knows (admissions only an owner
signed), and the owner's chain of gate rules (``policy``: signed bundles, v1
first). Nothing here starts on its own: pairing waits for the person, and
"online" lasts until they go offline or close Studio.

Rules across your devices: the owner signs the gate rules in their Studio;
the chain travels over the mesh (``mesh_rtc``) to every device. Here a
bundle is taken only if it is signed by an owner of this mesh and is the
next in the chain; ``on_rules`` (Presence) then offers it to this machine's
own node, which is the final judge, and what the node holds goes back to the
other devices (``report``).

The window reads `snapshot()` and calls the actions; the WebRTC work runs on
one asyncio loop in a daemon thread.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from pathlib import Path

from secdogie_identity import Identity

from .words import get_ui, pick, set_ui

MESH_FILE = "mesh.json"
DEFAULT_MESH = "home"
INVITE_TTL = 600


def qr_rows(text: str) -> list[str]:
    """The QR code for `text` as rows of '0' / '1' (a quiet zone of 2 included)."""
    import segno

    return ["".join("1" if cell else "0" for cell in row) for row in segno.make(text, error="m").matrix_iter(border=2)]


class Devices:
    def __init__(self, profile, *, lang=lambda: "zh", clock=time.time):
        self.profile = profile
        self.lang = lang
        self.clock = clock
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mesh = None  # MeshNode while online
        self._local = None  # LocalPairing while an invitation is open on this machine's network
        self._pair_host: str | None = None  # the address to serve on (tests pin it; else this machine's own)
        self._pair: dict = {"phase": "idle"}
        self._join: dict = {"phase": "idle"}
        self._notes: list[dict] = []
        self._decision: asyncio.Future | None = None
        self._task = None
        self._join_invite: dict | None = None
        self.on_rules = lambda chain: None  # a chain came in from another device: offer it to this node
        self._state = self._read()
        self._tiers: dict[str, str] = {}  # did -> active link tier ("lan" | "webrtc" | "relay")
        self._online_here = False  # True when go_online() started local LAN signaling itself

    # ---- what is kept --------------------------------------------------------------------

    @property
    def _path(self) -> Path:
        return Path(self.profile.root) / MESH_FILE

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {"mesh": DEFAULT_MESH, "role": "owner", "owners": [self.profile.operator_did()], "self": None,
                "devices": []}

    def _write(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    def _keep_device(self, admission: dict) -> None:
        devices = [a for a in self._state["devices"] if a.get("device") != admission["device"]]
        self._state["devices"] = [*devices, admission]
        self._write()

    # ---- settings -------------------------------------------------------------------------

    def settings(self) -> dict:
        ui = get_ui(self.profile.root)
        return {"signal": ui.get("signal", ""), "page": ui.get("pairPage", ""), "deviceName": self.device_name()}

    def set_settings(self, signal: str, page: str) -> None:
        set_ui(self.profile.root, signal=signal.strip(), pairPage=page.strip())

    def device_name(self) -> str:
        """What this machine is called among your devices: the person's choice, else its host name."""
        import socket

        from secdogie_transport.pairing import _name

        chosen = get_ui(self.profile.root).get("deviceName", "")
        return _name(chosen) or _name(socket.gethostname()) or "Studio"

    def set_device_name(self, name: str) -> None:
        set_ui(self.profile.root, deviceName=name.strip())

    # ---- the loop -------------------------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True, name="studio-devices").start()
        return self._loop

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())

    def _here(self):
        """Signaling and the pairing page on this machine's network, started once and kept while pairing."""
        from .local_pairing import LocalPairing

        if self._local is None or not self._local.running:
            self._local = LocalPairing(self._ensure_loop(), host=self._pair_host).start()
        return self._local

    def _stop_here(self) -> None:
        local, self._local = self._local, None
        if local is not None:
            local.stop()

    def _set(self, which: str, **fields) -> None:
        with self._lock:
            setattr(self, which, {**getattr(self, which), **fields} if fields.get("phase") is None else dict(fields))

    # ---- inviting a device ----------------------------------------------------------------

    def write_invite(self, passphrase: str, signal: str = "", page: str = "") -> None:
        """Unlock the operator key, write an invitation, and wait for its device.

        With no gateway given (the usual way), Studio runs signaling and the pairing page on this machine's
        own network, so nothing need be set up or typed. A ``ws://`` or ``wss://`` address instead uses the
        owner's own gateway, for a device that is not on this network."""
        from secdogie_dialogue.keystore import KeystoreError, unseal_identity
        from secdogie_transport.pairing import create_admission, create_invite, encode_invite

        w = lambda zh, en: pick(self.lang(), zh, en)  # noqa: E731
        if self._state.get("role") == "member":
            self._set("_pair", phase="ended", reason=w("这台电脑是别人网格的成员，只有主人能请新设备。",
                                                       "This computer is a member of someone else's mesh; only its owner can invite."))
            return
        here = not signal.strip()
        if here:
            try:
                local = self._here()
                signal, page = local.signal_url, local.page_url
            except OSError as e:
                self._set("_pair", phase="ended", reason=w(f"本机网络起不来：{e}", f"Could not start on this network: {e}"))
                return
        elif not signal.startswith(("ws://", "wss://")):
            self._set("_pair", phase="ended", reason=w("信令地址要以 ws:// 或 wss:// 开头。",
                                                       "The signaling address starts with ws:// or wss://."))
            return
        else:
            self.set_settings(signal, page)  # a custom gateway is remembered; the on-network one is not
        try:
            owner = unseal_identity(self.profile.operator_keystore, passphrase.encode("utf-8"))
        except (KeystoreError, ValueError, OSError):
            self._set("_pair", phase="ended", reason=w("口令不对，再输一次。", "That passphrase is wrong. Type it again."))
            return
        node = Identity.load(self.profile.node_key)
        if not self._state.get("self"):  # this machine becomes a device of its own mesh
            self._state["self"] = create_admission(owner, self._state["mesh"], node.did,
                                                   invite_id=secrets.token_hex(16), name=self.device_name(),
                                                   clock=self.clock)
            self._write()
        invite = create_invite(owner, self._state["mesh"], room=f"pair-{secrets.token_hex(16)}", signal=signal,
                               ttl=INVITE_TTL, clock=self.clock)
        token = encode_invite(invite)
        link = f"{page.rstrip('#')}#invite={token}" if page else ""
        self._set("_pair", phase="waiting", link=link, token=token, qr=qr_rows(link) if link else [],
                  here=here, expires=invite["expires_at"])
        roster = {"mesh": self._state["mesh"], "admissions": [self._state["self"], *self._state["devices"]]}
        self._task = self._run(self._invite(owner, invite, roster))

    async def _invite(self, owner, invite, roster) -> None:
        from secdogie_transport.pairing_session import PairingCancelled, PairingFailed, invite_device

        loop = asyncio.get_running_loop()
        self._decision = loop.create_future()

        def shown(c):
            self._set("_pair", **{**self._pair, "phase": "compare", "code": c.code, "name": c.name, "did": c.did})

        async def decide():
            return await self._decision

        try:
            admission = await invite_device(owner, invite, on_code=shown, decide=decide, roster=roster,
                                            timeout=max(1.0, invite["expires_at"] - self.clock()))
        except PairingCancelled:
            self._set("_pair", phase="ended", reason=pick(self.lang(), "没有接纳任何设备。", "No device was admitted."))
            return
        except (PairingFailed, Exception) as e:  # noqa: BLE001 - shown to the person
            self._set("_pair", phase="ended", reason=str(e))
            return
        finally:
            del owner  # the operator key lives only as long as this pairing
        self._keep_device(admission)
        self._set("_pair", phase="done", name=admission.get("name", ""), did=admission["device"])
        if self._mesh is not None:
            self._mesh.admit(admission)

    def decide(self, admit: bool) -> None:
        fut, loop = self._decision, self._loop
        if fut is not None and loop is not None and self._pair.get("phase") == "compare":
            self._set("_pair", **{**self._pair, "phase": "admitting" if admit else "ending"})
            loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(bool(admit)))

    def withdraw(self) -> None:
        if self._task is not None:
            self._task.cancel()
        self._stop_here()
        self._set("_pair", phase="idle")

    # ---- being invited --------------------------------------------------------------------

    def read_invite(self, text: str) -> None:
        """Check an invitation (a link or its code) offline, before anything connects."""
        from secdogie_transport.pairing import decode_invite, verify_invite

        token = text.strip().split("#invite=", 1)[-1]
        checked = verify_invite(decode_invite(token), clock=self.clock)
        if not checked.ok:
            self._set("_join", phase="ended", reason=checked.reason)
            self._join_invite = None
            return
        self._join_invite = checked.value
        inv = checked.value
        self._set("_join", phase="card", inviter=inv["inviter"], mesh=inv["mesh"], until=inv["expires_at"],
                  signal=inv["signal"])

    def agree(self) -> None:
        invite = self._join_invite
        if invite is None or self._join.get("phase") != "card":
            return
        self._set("_join", **{**self._join, "phase": "joining"})
        self._task = self._run(self._joining(invite))

    async def _joining(self, invite) -> None:
        from secdogie_transport.pairing_session import PairingCancelled, join_with_invite

        node = Identity.load(self.profile.node_key)

        def shown(c):
            self._set("_join", **{**self._join, "phase": "compare", "code": c.code})

        try:
            admission, roster = await join_with_invite(node, invite, my_name=self.device_name(),
                                                       on_code=shown)
        except PairingCancelled:
            self._set("_join", phase="ended", reason=pick(self.lang(), "对方取消了。", "The other side cancelled."))
            return
        except Exception as e:  # noqa: BLE001 - shown to the person
            self._set("_join", phase="ended", reason=str(e))
            return
        self._state = {"mesh": invite["mesh"], "role": "member", "owners": [invite["inviter"]], "self": admission,
                       "devices": [a for a in (roster or {}).get("admissions", []) if isinstance(a, dict)], "policy": []}
        self._write()
        try:  # the node follows this mesh's name, so the owner's rules are for it (it restarts to take it)
            self.profile.set_setting("mesh", invite["mesh"])
        except Exception:  # noqa: BLE001 - a profile without settings (tests): the mesh file is enough
            pass
        self.set_settings(invite["signal"], self.settings()["page"])
        self._set("_join", phase="done", mesh=invite["mesh"])

    def reset_join(self) -> None:
        self._join_invite = None
        self._set("_join", phase="idle")

    # ---- online ---------------------------------------------------------------------------

    def go_online(self, signal: str = "") -> None:
        from secdogie_transport.hybrid_tunnel import TIER_LAN, TIER_WEBRTC
        from secdogie_transport.mesh_rtc import MeshNode
        from secdogie_transport.star import ROLE_HUB, ROLE_SPOKE

        if self._mesh is not None or not self._state.get("self"):
            return
        sig = (signal or "").strip()
        here = not sig
        if here:
            try:
                local = self._here()
                sig = local.signal_url
            except OSError:
                return
        elif not sig.startswith(("ws://", "wss://")):
            return
        else:
            self.set_settings(sig, self.settings()["page"])
        node = Identity.load(self.profile.node_key)
        topo_role = ROLE_HUB if self._state.get("role", "owner") == "owner" else ROLE_SPOKE
        is_lan_sig = here or sig.startswith(("ws://127.", "ws://192.168.", "ws://10.", "ws://172.", "ws://localhost"))
        mesh = MeshNode(node, mesh=self._state["mesh"], owners=self._state["owners"], admission=self._state["self"],
                        admissions=self._state["devices"], name=self.device_name(), signal_url=sig,
                        role=topo_role, default_tier=TIER_LAN if is_lan_sig else TIER_WEBRTC,
                        on_note=self._note, on_policy=self._incoming)
        mesh.set_chain(self.chain())
        self._mesh = mesh
        self._online_here = here
        self._run(mesh.start())

    def set_peer_tier(self, did: str, tier: str) -> None:
        """Record the active HybridTunnel tier ('lan' | 'webrtc' | 'relay') for a device."""
        from secdogie_transport.hybrid_tunnel import TIER_ORDER

        if tier not in TIER_ORDER:
            return
        with self._lock:
            self._tiers[did] = tier
        mesh = self._mesh
        if mesh is not None and did in mesh.links:
            mesh.links[did].tier = tier

    def go_offline(self) -> None:
        mesh, self._mesh = self._mesh, None
        online_here, self._online_here = self._online_here, False
        if mesh is not None:
            self._run(mesh.stop())
        if online_here and self._pair.get("phase") not in ("waiting", "compare", "admitting"):
            self._stop_here()

    def say(self, text: str) -> None:
        mesh = self._mesh
        if mesh is None or not text.strip():
            return
        self._note({"from": "", "name": "", "text": text.strip(), "at": self.clock(), "mine": True})
        self._run(mesh.say(text))

    # ---- rules across your devices ---------------------------------------------------------

    def chain(self) -> list[dict]:
        with self._lock:
            return list(self._state.get("policy") or [])

    def verify(self, bundle: dict) -> bool:
        """A gate-rules bundle for this mesh, for every device of it, signed by an owner of it."""
        from secdogie_identity import Allowlist
        from secdogie_policy import verify_bundle

        if not isinstance(bundle, dict) or bundle.get("kind") != "gate-rules" or bundle.get("mesh") != self._state["mesh"]:
            return False
        if bundle.get("scope") != {"nodes": [], "roles": []}:
            return False
        owners = Allowlist(set(self._state.get("owners") or ()))
        return verify_bundle(bundle, operators=owners).ok

    def _incoming(self, bundle: dict, frm: str) -> bool:
        """A bundle from another device (on the devices loop): keep it if it holds, then hand the chain on."""
        if not self.verify(bundle):
            return False
        with self._lock:
            chain = list(self._state.get("policy") or [])
            if bundle["version"] != len(chain) + 1:
                return False
            chain.append(bundle)
            self._state["policy"] = chain
            self._write()
        try:
            self.on_rules(list(chain))
        except Exception:  # noqa: BLE001 - offering it to the node is the caller's; the chain is kept either way
            pass
        return True

    def spread(self, chain: list[dict]) -> None:
        """The owner's own chain (as its node holds it): keep it and send every device what it lacks."""
        chain = [b for b in chain if self.verify(b)]
        with self._lock:
            self._state["policy"] = chain
            self._write()
        mesh = self._mesh
        if mesh is not None:
            mesh.set_chain(chain)
            self._run(mesh.spread())

    def report(self, active, errors=()) -> None:
        """What this machine's node holds now, for the other devices."""
        mesh = self._mesh
        if mesh is not None:
            self._run(mesh.report(active, errors))

    def _note(self, note: dict) -> None:
        with self._lock:
            self._notes = [{**note, "mine": bool(note.get("mine"))}, *self._notes][:50]

    def close(self) -> None:
        self.go_offline()
        self._stop_here()
        if self._task is not None:
            self._task.cancel()

    # ---- what the window reads ------------------------------------------------------------

    def snapshot(self) -> dict:
        from secdogie_transport.hybrid_tunnel import TIER_WEBRTC, tier_label
        from secdogie_transport.meshlink import Roster
        from secdogie_transport.star import ROLE_HUB, ROLE_SPOKE

        lang = self.lang()
        with self._lock:
            pair, join, notes = dict(self._pair), dict(self._join), list(self._notes)
            tiers = dict(self._tiers)
        mesh = self._mesh
        topo_role = ROLE_HUB if self._state.get("role", "owner") == "owner" else ROLE_SPOKE
        if mesh is not None:
            view = mesh.snapshot()
            devices = []
            for d in view["devices"]:
                t = tiers.get(d["did"], d.get("tier") or TIER_WEBRTC)
                devices.append({
                    **d,
                    "role": d.get("role") or (ROLE_HUB if d.get("owner") else ROLE_SPOKE),
                    "tier": t,
                    "tierLabel": tier_label(t, lang),
                    "unacked": int(d.get("unacked", 0)),
                })
        else:
            roster = Roster(self._state["mesh"], self._state["owners"])
            for a in [self._state.get("self"), *self._state["devices"]]:
                if a:
                    roster.admit(a)
            me = self._state["self"]["device"] if self._state.get("self") else ""
            held = len(self._state.get("policy") or [])
            view = {"online": False}
            devices = []
            for d in roster.devices():
                if d == me or not roster.admission(d):
                    continue
                t = tiers.get(d, TIER_WEBRTC)
                devices.append({
                    "did": d, "name": roster.name(d), "owner": d in roster.owners,
                    "role": ROLE_HUB if d in roster.owners else ROLE_SPOKE,
                    "tier": t, "tierLabel": tier_label(t, lang), "unacked": 0,
                    "state": "offline", "rtt": None, "reason": None,
                    "rules": {"version": 0, "state": "waiting" if held else "none"},
                })
        left = max(0, int(pair.get("expires", 0) - self.clock())) if pair.get("phase") in ("waiting", "compare") else 0
        return {
            "role": self._state.get("role", "owner"), "topologyRole": topo_role,
            "mesh": self._state["mesh"],
            "ready": bool(self._state.get("self")), "online": view["online"], "devices": devices,
            "pair": {**{k: v for k, v in pair.items() if k != "token"}, "left": left,
                     "code_text": pair.get("token", "")},
            "join": join, "notes": notes, "rulesVersion": len(self._state.get("policy") or []), **self.settings(),
        }


__all__ = ["Devices", "qr_rows"]
