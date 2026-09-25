"""The node's configuration file.

Same `key = value` shape as the allowlist and the tunnel config: `#` comments,
blank lines ignored, repeatable keys for lists, errors reported as `path:line`.

    identity      = node.key            # this node's DID signing key (required)
    journal       = node.db             # the signed journal, sqlite (required)
    listen        = 0.0.0.0:7400        # UDP host:port (required)
    authorized    = authorized.conf     # allowlist of node DIDs (required)
    transport_key = node.tkey           # optional: tunnel key file -> encrypted v2 frames
    evidence      = evidence/           # optional: content-addressed evidence cache dir
    peer          = did:key:z... 203.0.113.7:7400   # bootstrap peer (repeatable)
    binding       = peers/b.binding.json            # a peer's signed binding (repeatable)
    announce_host = 203.0.113.9         # optional: the address other nodes should use
    sync_interval = 15                  # seconds between rounds (default 15)
    key_version   = 1                   # this node's binding key_version (default 1)

Relative paths are resolved against the config file's directory.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from secdogie_identity.did import pubkey_from_did

_REQUIRED = ("identity", "journal", "listen", "authorized")
_PATH_KEYS = ("identity", "journal", "authorized", "transport_key", "evidence")
_KNOWN = set(_REQUIRED) | {"transport_key", "evidence", "peer", "binding", "announce_host", "sync_interval", "key_version"}


class NodeConfigError(ValueError):
    pass


@dataclass
class NodeConfig:
    identity: str
    journal: str
    listen_host: str
    listen_port: int
    authorized: str
    transport_key: str | None = None
    evidence: str | None = None
    peers: list[tuple[str, str, int]] = field(default_factory=list)  # (did, host, port)
    bindings: list[str] = field(default_factory=list)
    announce_host: str | None = None
    sync_interval: float = 15.0
    key_version: int = 1


def parse_hostport(text: str) -> tuple[str, int]:
    host, sep, port = text.strip().rpartition(":")
    if not sep or not host:
        raise ValueError(f"expected host:port, got {text!r}")
    try:
        n = int(port)
    except ValueError:
        raise ValueError(f"bad port in {text!r}") from None
    if not 0 <= n < 65536:
        raise ValueError(f"port out of range in {text!r}")
    return host.strip("[]"), n


def load_config(path: str | os.PathLike) -> NodeConfig:
    base = Path(path).resolve().parent
    values: dict[str, str] = {}
    peers: list[tuple[str, str, int]] = []
    bindings: list[str] = []

    def where(lineno: int) -> str:
        return f"{path}:{lineno}"

    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        s = raw.split("#", 1)[0].strip()
        if not s:
            continue
        if "=" not in s:
            raise NodeConfigError(f"{where(lineno)}: expected 'key = value'")
        key, _, val = (x.strip() for x in s.partition("="))
        if key not in _KNOWN:
            raise NodeConfigError(f"{where(lineno)}: unknown key {key!r}")
        if not val:
            raise NodeConfigError(f"{where(lineno)}: empty value for {key!r}")
        try:
            if key == "peer":
                parts = val.split()
                if len(parts) != 2:
                    raise ValueError("expected 'peer = <did> <host:port>'")
                pubkey_from_did(parts[0])
                peers.append((parts[0], *parse_hostport(parts[1])))
            elif key == "binding":
                bindings.append(str(base / val))
            elif key == "listen":
                parse_hostport(val)
                values[key] = val
            elif key == "sync_interval":
                if float(val) <= 0:
                    raise ValueError("sync_interval must be positive")
                values[key] = val
            elif key == "key_version":
                if int(val) < 1:
                    raise ValueError("key_version must be >= 1")
                values[key] = val
            else:
                values[key] = val
        except ValueError as exc:
            raise NodeConfigError(f"{where(lineno)}: {exc}") from None

    missing = [k for k in _REQUIRED if k not in values]
    if missing:
        raise NodeConfigError(f"{path}: missing required key(s): {', '.join(missing)}")
    for k in _PATH_KEYS:
        if k in values:
            values[k] = str(base / values[k])
    host, port = parse_hostport(values["listen"])
    return NodeConfig(
        identity=values["identity"],
        journal=values["journal"],
        listen_host=host,
        listen_port=port,
        authorized=values["authorized"],
        transport_key=values.get("transport_key"),
        evidence=values.get("evidence"),
        peers=peers,
        bindings=bindings,
        announce_host=values.get("announce_host"),
        sync_interval=float(values.get("sync_interval", 15.0)),
        key_version=int(values.get("key_version", 1)),
    )


__all__ = ["NodeConfig", "NodeConfigError", "load_config", "parse_hostport"]
