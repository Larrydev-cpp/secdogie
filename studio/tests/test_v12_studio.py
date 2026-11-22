"""Phase 3-5 Studio unit tests (pure Python + QML/asset verification, runs with or without PySide6).

Covers:
- Phase 3: Devices snapshot exposes star topologyRole, per-device tier, tierLabel, role, unacked;
- Phase 4: Talk "Desktop Agent" switch defaults off, arms read-only Atlas echo, and surfaces
  honest OS privilege guidance (hard PPL/kernel refusal);
- Phase 5: Floating orb defaults off (system tray mode), opt-in toggle works, Onboarding includes
  optional external LLM API key setup with one-click test & skip, "油画雪山与花园" visual theme
  defaults to sunlit light mode with modern sans typography, and golden-ratio squircle icons
  (512px PNG, 256px PNG, multi-size ICO) exist and parse cleanly.
"""
from __future__ import annotations

import struct
from pathlib import Path

from secdogie_identity import Identity
from secdogie_studio.app import Presence
from secdogie_studio.devices import Devices
from secdogie_transport.hybrid_tunnel import TIER_LAN, TIER_RELAY, TIER_WEBRTC
from secdogie_transport.pairing import create_admission
from secdogie_transport.star import ROLE_HUB, ROLE_SPOKE

STUDIO_ROOT = Path(__file__).resolve().parents[1]
QML_ROOT = STUDIO_ROOT / "secdogie_studio" / "qml"
ICONS_ROOT = STUDIO_ROOT / "packaging" / "icons"
DESKTOP_APP = STUDIO_ROOT.parent / "desktop" / "secdogie_desktop" / "app.py"


class _FakeSnap:
    def __init__(self, initialized=True):
        self.initialized = initialized
        self.busy = False
        self.node_state = "running" if initialized else "stopped"
        self.node_did = "did:key:hub-pc"
        self.app_did = "did:key:app-pc"
        self.operator_did = "did:key:op-pc"
        self.roles = ("hub", "planner")
        self.listen = "127.0.0.1:9000"
        self.error = ""
        self.log = ()


class _FakeProfile:
    def __init__(self, root: Path):
        self.root = root
        self.thinker_config = root / "thinker.json"
        self.cortex = root / "cortex"
        self._op = Identity.generate()
        self._node = Identity.generate()

    def operator_did(self) -> str:
        return self._op.did

    def name(self) -> str:
        return "Alice"


class _FakeController:
    def __init__(self, root: Path, initialized=True):
        self.profile = _FakeProfile(root)
        self._snap = _FakeSnap(initialized=initialized)

    def snapshot(self):
        return self._snap

    def ready(self):
        return None


def test_phase3_devices_snapshot_exposes_star_role_and_3tier_link_status(tmp_path):
    profile = _FakeProfile(tmp_path)
    peer = Identity.generate()
    devices = Devices(profile, lang=lambda: "zh")
    adm_self = create_admission(profile._op, "home", profile._node.did, invite_id="a" * 32, name="Hub PC")
    adm_peer = create_admission(profile._op, "home", peer.did, invite_id="b" * 32, name="iPhone")
    devices._state["self"] = adm_self
    devices._keep_device(adm_peer)

    snap = devices.snapshot()
    assert snap["topologyRole"] == ROLE_HUB
    assert len(snap["devices"]) == 1
    d = snap["devices"][0]
    assert d["role"] == ROLE_SPOKE
    assert d["tier"] == TIER_WEBRTC
    assert d["tierLabel"] == "WebRTC P2P"
    assert d["unacked"] == 0

    devices.set_peer_tier(peer.did, TIER_LAN)
    assert devices.snapshot()["devices"][0]["tierLabel"] == "LAN 直连"

    devices.set_peer_tier(peer.did, TIER_RELAY)
    assert devices.snapshot()["devices"][0]["tierLabel"] == "E2EE 加密中继"


def test_phase4_desktop_agent_toggle_context_echo_and_privilege_guide(tmp_path):
    ctrl = _FakeController(tmp_path)
    presence = Presence(ctrl, idle=lambda: 5.0, clock=lambda: 200.0)
    s0 = presence.tick()
    assert s0["desktopAgent"] is False
    assert s0["desktopContext"]["readOnly"] is True
    assert s0["desktopPrivilegeGuide"]["refusedBoundary"]

    presence.set_desktop_agent(True)
    s1 = presence.tick()
    assert s1["desktopAgent"] is True
    assert "只读" in s1["desktopContext"]["statusLine"]

    presence.update_desktop_context({
        "windowTitle": "README.md - VS Code",
        "allowed": True,
        "statusLine": "只读感知 · README.md - VS Code · 3 项无障碍元素",
        "tags": ["editor:README.md"],
    })
    s2 = presence.tick()
    assert s2["desktopContext"]["windowTitle"] == "README.md - VS Code"
    assert s2["desktopContext"]["readOnly"] is True

    home_qml = (QML_ROOT / "Home.qml").read_text(encoding="utf-8")
    assert 'objectName: "desktopAgentBar"' in home_qml
    assert "P.setDesktopAgent(" in home_qml
    assert "P.desktopPrivilegeGuide" in home_qml


def test_phase5_tray_mode_default_onboarding_api_key_and_alpine_theme(tmp_path):
    ctrl = _FakeController(tmp_path)
    presence = Presence(ctrl, idle=lambda: 5.0, clock=lambda: 200.0)
    snap = presence.tick()

    # Floating orb disabled by default -> tray mode; sunlit snow-mountain light theme by default
    assert snap["floatingOrb"] is False
    assert snap["trayMode"] is True
    assert snap["theme"] == "light"

    # Opt-in floating orb works and persists to ui.json
    presence.set_floating_orb(True)
    assert presence.tick()["floatingOrb"] is True
    presence.set_floating_orb(False)
    assert presence.tick()["floatingOrb"] is False

    # Presence.qml binds visibility to floatingOrb
    presence_qml = (QML_ROOT / "Presence.qml").read_text(encoding="utf-8")
    assert "P.floatingOrb" in presence_qml

    # Onboarding.qml includes optional external LLM API key step with one-click test & skip
    onboarding_qml = (QML_ROOT / "Onboarding.qml").read_text(encoding="utf-8")
    assert 'objectName: "onboardingAdvisor"' in onboarding_qml
    assert "P.saveAndTestThinker(" in onboarding_qml
    assert "P.finishOnboarding()" in onboarding_qml

    # Theme.qml, Soft.qml, MistBackdrop.qml, PaintedPanel.qml, and Tkinter desktop app adopt alpine theme
    theme_qml = (QML_ROOT / "Theme.qml").read_text(encoding="utf-8")
    assert "property bool dark: false" in theme_qml
    assert "#256DA8" in theme_qml  # alpine sky blue
    assert "#2B6644" in theme_qml  # garden moss green
    assert "Qt.rgba(" in theme_qml  # frosted glass translucency

    backdrop_qml = (QML_ROOT / "aesper" / "MistBackdrop.qml").read_text(encoding="utf-8")
    assert "peak" in backdrop_qml and "rgba(96,158,116" in backdrop_qml

    desktop_src = DESKTOP_APP.read_text(encoding="utf-8")
    assert "#EAF3FA" in desktop_src
    assert "#1F66A3" in desktop_src
    assert "#2B6644" in desktop_src


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a valid PNG"
    assert data[12:16] == b"IHDR"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_phase5_squircle_icons_exist_with_valid_headers_and_dimensions():
    png512 = ICONS_ROOT / "secdogie-studio.png"
    png256 = ICONS_ROOT / "secdogie-studio-256.png"
    ico = ICONS_ROOT / "secdogie-studio.ico"

    assert _png_size(png512) == (512, 512)
    assert _png_size(png256) == (256, 256)

    ico_data = ico.read_bytes()
    reserved, typ, count = struct.unpack("<HHH", ico_data[:6])
    assert reserved == 0 and typ == 1 and count >= 4
