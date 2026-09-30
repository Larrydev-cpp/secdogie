"""The window's own node, on this machine, with nothing to set up.

The window is the operator's App. What it talks to is the ordinary resident
node (``secdogie_node.Node``), run in the same process and bound to 127.0.0.1
only. The pair is wired exactly as a remote one is: separate keys, the signed
dialogue over a DID-authenticated UDP transport, allowlists that name each
other. Every check the node makes on a remote App it makes on this one; being
local loosens nothing.

The first run creates, in the user's config directory (``default_home()``):

  * ``node.key`` -- the node's identity (0600);
  * ``app.key`` -- the window's session key. It signs envelopes and memory
    confirmations, never a Gate 2 approval (0600);
  * ``journal.db`` / ``memory.db`` -- the node's signed journal and its memory
    quarantine.

The operator key is not made here. It is made at the first Gate 2 approval,
under a passphrase the operator sets in the window (``OperatorKey``), and it
exists on disk only sealed (``operator.keystore``, see ``dialogue.keystore``).
From then on each approval unseals it for that one signature. The node trusts
exactly that key for Gate 2, so until it exists nothing high-risk can be
approved at all.

Capabilities still apply. At every start a fresh issuer key grants this node
the desktop scopes (``DESKTOP_SCOPES``: look, click, type, press keys, scroll,
drag, open) and nothing else: no process.run, no network. The issuer key lives
in memory only and is never written anywhere. A grant never stands in for
Gate 2: a high-risk step still needs the operator's signature.

One window per user at a time: a second one is refused (``AlreadyRunning``)
rather than sharing the journal.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from secdogie_citadel.supervisor import agent_run_task
from secdogie_dialogue.app import AppController
from secdogie_dialogue.connect import open_session
from secdogie_dialogue.keystore import KeystoreError, keystore_did, seal_identity, unseal_identity
from secdogie_identity import Allowlist, Identity
from secdogie_identity.capability import create_capability
from secdogie_node import Node, NodeConfig

DESKTOP_SCOPES = ("observe.read", "physical.click", "physical.type", "physical.key", "physical.scroll",
                  "physical.drag", "system.open")
GRANT_TTL = 24 * 3600.0  # a grant lapses unless renewed; the window renews it at half-life
MIN_PASSPHRASE = 8


class AlreadyRunning(RuntimeError):
    """Another secdogie window already has this config directory."""


def default_home() -> Path:
    """Where the window keeps its keys and journal: ``SECDOGIE_HOME`` if set,
    else the platform's per-user config directory."""
    env = os.environ.get("SECDOGIE_HOME", "").strip()
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "secdogie" / "app"


def passphrase_problem(passphrase: str, again: str) -> str | None:
    """Why a new passphrase cannot be set, or None."""
    if not passphrase:
        return "请设置一个口令。"
    if len(passphrase) < MIN_PASSPHRASE:
        return f"口令至少 {MIN_PASSPHRASE} 个字符。"
    if passphrase != again:
        return "两次输入的口令不一致，口令没有设置。"
    return None


class OperatorKey:
    """The operator's Gate 2 key: made at the first approval, sealed under the
    operator's passphrase, and trusted by the node (``trusted``) from then on.
    The passphrase is never stored; the key is returned for one signature."""

    def __init__(self, path: Path, trusted: Allowlist, *, kdf: dict | None = None):
        self.path = Path(path)
        self._trusted = trusted
        self._kdf = dict(kdf or {})
        self._lock = threading.Lock()
        self._did: str | None = None
        if self.path.exists():
            self._did = keystore_did(self.path)  # pinned: a keystore swapped in later is refused
            trusted.add(self._did)

    @property
    def is_set(self) -> bool:
        return self._did is not None

    @property
    def did(self) -> str | None:
        return self._did

    def create(self, passphrase: str, again: str) -> Identity:
        """Make the operator key, seal it under ``passphrase`` and trust it.
        Refuses a short passphrase, a mismatched pair, and a key already set."""
        problem = passphrase_problem(passphrase, again)
        if problem:
            raise KeystoreError(problem)
        with self._lock:
            if self._did is not None or self.path.exists():
                raise KeystoreError("口令已经设置过了。")
            identity = Identity.generate()
            seal_identity(identity, passphrase.encode("utf-8"), self.path, **self._kdf)
            self._did = identity.did
            self._trusted.add(identity.did)
        return identity

    def unlock(self, passphrase: str) -> Identity:
        """The operator key, for one signature. A wrong passphrase raises
        ``KeystoreError``."""
        if self._did is None:
            raise KeystoreError("还没有设置口令。")
        return unseal_identity(self.path, passphrase.encode("utf-8"), expected_did=self._did)


class ApiKeys:
    """The model's API key, kept where the agent reads it (``secdogie_agent.config``:
    next to the exe when packaged, else the user's config file)."""

    PROVIDERS = ("anthropic", "openai", "openrouter")

    def configured(self) -> bool:
        from secdogie_agent import config

        return config.has_configured_api_key()

    def problem(self, key: str) -> str | None:
        from secdogie_agent import config

        return config.api_key_problem(key)

    def save(self, key: str, *, provider: str | None = None, model: str | None = None) -> Path:
        """Write the key (an ``sk-or-`` / ``sk-ant-`` key names its own provider)."""
        from secdogie_agent import config

        problem = config.api_key_problem(key)
        if problem:
            raise ValueError(problem)
        if provider is not None and provider not in self.PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        return config.write_api_key(key.strip(), provider=provider, model=(model or "").strip() or None)


def _load_or_create(path: Path) -> Identity:
    if path.exists():
        return Identity.load(path)
    identity = Identity.generate()
    identity.save(path)
    return identity


class _InstanceLock:
    """An exclusive lock on a file in the config directory, held while the
    window runs; the OS drops it if the process dies."""

    def __init__(self, path: Path):
        self._f = open(path, "a+")  # noqa: SIM115 - held open for the life of the window
        try:
            self._f.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._f.close()
            raise AlreadyRunning("another secdogie window is already open") from None

    def release(self) -> None:
        self._f.close()


class LocalBackend:
    """The node and the App, wired together on 127.0.0.1. ``start()`` returns
    the ``AppController`` the window drives; ``stop()`` closes everything."""

    def __init__(self, home: Path | None = None, *, run_task=agent_run_task, kdf: dict | None = None,
                 challenge_ttl: float = 120.0, probe_ttl: float = 300.0, grant_ttl: float = GRANT_TTL):
        self.home = Path(home) if home is not None else default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.home, 0o700)
        except OSError:
            pass
        self._instance = _InstanceLock(self.home / "window.lock")
        try:
            self.node_identity = _load_or_create(self.home / "node.key")
            self.app_identity = _load_or_create(self.home / "app.key")
            self.operators = Allowlist()
            self.operator_key = OperatorKey(self.home / "operator.keystore", self.operators, kdf=kdf)
            self._issuer = Identity.generate()  # memory only: grants the desktop scopes, nothing else
            me = Allowlist({self.node_identity.did})
            self.node = Node(NodeConfig(
                identity=self.node_identity, apps=Allowlist({self.app_identity.did}), operators=self.operators,
                authorized=me, mesh=me, issuers=Allowlist({self._issuer.did}),
                journal_path=str(self.home / "journal.db"), candidates_path=str(self.home / "memory.db"),
                listen=("127.0.0.1", 0), run_task=run_task, challenge_ttl=challenge_ttl, probe_ttl=probe_ttl,
            ))
        except Exception:
            self._instance.release()
            raise
        self._grant_ttl = float(grant_ttl)
        self._stopping = threading.Event()
        self._renewer: threading.Thread | None = None
        self._app = None  # the window's session with this node (dialogue.connect.AppLink)
        self.controller: AppController | None = None

    def grant(self) -> dict:
        """Grant this node the desktop scopes for ``grant_ttl`` seconds."""
        return self.node.supervisor.add_grant(
            create_capability(self._issuer, self.node_identity.did, DESKTOP_SCOPES, ttl=self._grant_ttl))

    def _renew(self) -> None:
        while not self._stopping.wait(self._grant_ttl / 2):
            try:
                self.grant()
            except Exception:  # noqa: BLE001 - try again at the next half-life
                pass

    def start(self) -> AppController:
        self.grant()
        self.node.start()
        self._app = open_session(self.app_identity, self.node_identity.did, listen=("127.0.0.1", 0),
                                 node_addr=self.node.address)
        ctl = self.controller = self._app.controller
        self._renewer = threading.Thread(target=self._renew, daemon=True, name="secdogie-grant-renewal")
        self._renewer.start()
        return ctl

    def stop(self) -> None:
        self._stopping.set()
        try:
            if self._app is not None:
                self._app.close()
            self.node.stop()
        finally:
            self._instance.release()


__all__ = [
    "DESKTOP_SCOPES",
    "GRANT_TTL",
    "MIN_PASSPHRASE",
    "AlreadyRunning",
    "ApiKeys",
    "LocalBackend",
    "OperatorKey",
    "default_home",
    "passphrase_problem",
]
