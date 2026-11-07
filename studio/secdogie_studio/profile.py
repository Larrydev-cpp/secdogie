"""The Studio profile: one directory with this machine's keys, trust lists and
settings, created once by the first-run wizard and never overwritten.

    node.key            the node's DID key (0600)
    app.key             Studio's own App session key: talks to the node (0600)
    operator.keystore   the operator key, sealed under a passphrase (Argon2id +
                        SecretBox, dialogue/keystore.py); unlocked per signature
    apps.allow          the App key the node accepts a dialogue from
    operators.allow     the operator key whose Gate 2 signatures the node accepts
    nodes.allow         journal authors: this node
    issuers.allow       who may grant this node capabilities: the operator.
                        Nothing is granted at first run, so every mutating
                        action stays refused until the operator grants it.
    node.db             the node's signed journal
    studio.json         settings; written last, so its presence means "complete"

Three keys, three jobs: the node key signs what the node does, the App key
talks, and only the operator key -- encrypted at rest, unlocked with a
passphrase for one signature at a time -- authorizes anything.

Nothing here starts, installs or schedules anything.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from nacl import pwhash
from secdogie_dialogue.keystore import keystore_did, seal_identity
from secdogie_identity import Allowlist, Identity

SETTINGS_VERSION = 1
ROLES = ("forward", "serve", "model", "ingest", "relay")  # what a node may offer the mesh
DEFAULT_ROLES = ("forward",)
DEFAULT_LISTEN = "127.0.0.1:0"
DEFAULT_MESH = "home"
MIN_PASSPHRASE = 8

_FILES = ("node.key", "app.key", "operator.keystore", "apps.allow", "operators.allow", "nodes.allow",
          "issuers.allow", "studio.json")
_KDF = {
    "moderate": (pwhash.argon2id.OPSLIMIT_MODERATE, pwhash.argon2id.MEMLIMIT_MODERATE),
    # Only for throwaway profiles (the self-test, tests): the minimum Argon2id cost.
    "min": (pwhash.argon2id.OPSLIMIT_MIN, pwhash.argon2id.MEMLIMIT_MIN),
}


class ProfileError(ValueError):
    """The profile cannot be created or read as asked."""


def default_root() -> Path:
    """Where a profile lives by default, per OS. Keys are user data, so this
    is the per-user data directory, never a shared or system location."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "secdogie-studio"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "secdogie-studio"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "secdogie-studio"


def parse_listen(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 <= int(port) <= 65535:
        raise ProfileError(f"listen address must be HOST:PORT, got {value!r}")
    return host, int(port)


@dataclass(frozen=True)
class Profile:
    root: Path

    def __post_init__(self):
        object.__setattr__(self, "root", Path(self.root))

    def path(self, name: str) -> Path:
        return self.root / name

    @property
    def node_key(self) -> Path:
        return self.path("node.key")

    @property
    def app_key(self) -> Path:
        return self.path("app.key")

    @property
    def operator_keystore(self) -> Path:
        return self.path("operator.keystore")

    @property
    def journal(self) -> Path:
        return self.path("node.db")

    @property
    def settings_path(self) -> Path:
        return self.path("studio.json")

    @property
    def initialized(self) -> bool:
        return self.settings_path.is_file()

    def settings(self) -> dict:
        if not self.initialized:
            raise ProfileError(f"no Studio profile at {self.root} (run the first-run wizard)")
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ProfileError(f"{self.settings_path}: {e}") from None
        if not isinstance(data, dict) or data.get("version") != SETTINGS_VERSION:
            raise ProfileError(f"{self.settings_path}: not a version {SETTINGS_VERSION} Studio profile")
        return data

    def node_did(self) -> str:
        return Identity.load(self.node_key).did

    def app_did(self) -> str:
        return Identity.load(self.app_key).did

    def operator_did(self) -> str:
        return keystore_did(self.operator_keystore)

    def node_args(self) -> list[str]:
        """The ``secdogie-node run`` arguments for this profile. The node stops
        when its standard input closes, so it never outlives Studio."""
        settings = self.settings()
        listen = settings.get("listen", DEFAULT_LISTEN)
        parse_listen(listen)
        roles = [r for r in settings.get("roles", DEFAULT_ROLES) if r in ROLES] or list(DEFAULT_ROLES)
        return ["run",
                "--identity", str(self.node_key),
                "--apps", str(self.path("apps.allow")),
                "--operators", str(self.path("operators.allow")),
                "--authorized", str(self.path("nodes.allow")),
                "--issuers", str(self.path("issuers.allow")),
                "--journal", str(self.journal),
                "--listen", listen,
                "--mesh", settings.get("mesh", DEFAULT_MESH),
                *[arg for role in roles for arg in ("--role", role)],
                "--exit-on-stdin-eof"]


def _write_allow(path: Path, did: str, what: str) -> None:
    Allowlist().add(did)  # validates the DID
    path.write_text(f"# {what} (written by secdogie-studio first run)\nauthorized_did = {did}\n",
                    encoding="utf-8")


def create_profile(root: str | os.PathLike, passphrase: bytes, *, roles=DEFAULT_ROLES,
                   listen: str = DEFAULT_LISTEN, kdf: str = "moderate", clock=time.time) -> Profile:
    """Create a complete profile at ``root``. Refuses to touch a directory that
    already holds any profile file, so a key is never overwritten -- not even
    one left by an interrupted first run."""
    root = Path(root)
    if not isinstance(passphrase, bytes) or len(passphrase) < MIN_PASSPHRASE:
        raise ProfileError(f"the operator passphrase needs at least {MIN_PASSPHRASE} characters")
    roles = tuple(roles)
    if not roles or any(r not in ROLES for r in roles) or len(set(roles)) != len(roles):
        raise ProfileError(f"roles must be distinct and among {', '.join(ROLES)}")
    parse_listen(listen)
    if kdf not in _KDF:
        raise ProfileError(f"unknown key-derivation cost {kdf!r}")
    existing = [n for n in _FILES if (root / n).exists()]
    if existing:
        raise ProfileError(f"{root} already holds {', '.join(existing)}; refusing to overwrite keys")
    root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(root, 0o700)

    node, app, operator = Identity.generate(), Identity.generate(), Identity.generate()
    node.save(root / "node.key")
    app.save(root / "app.key")
    ops, mem = _KDF[kdf]
    seal_identity(operator, passphrase, root / "operator.keystore", opslimit=ops, memlimit=mem)
    _write_allow(root / "apps.allow", app.did, "the Studio App this node talks to")
    _write_allow(root / "operators.allow", operator.did, "Gate 2 operator")
    _write_allow(root / "nodes.allow", node.did, "journal authors")
    _write_allow(root / "issuers.allow", operator.did, "who may grant this node capabilities")
    settings = {"version": SETTINGS_VERSION, "roles": sorted(roles), "listen": listen, "mesh": DEFAULT_MESH,
                "mdns": False,
                "keep_node_after_close": False, "created_at": int(clock())}
    tmp = root / "studio.json.tmp"
    tmp.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, root / "studio.json")
    return Profile(root)
