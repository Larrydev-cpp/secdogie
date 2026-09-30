"""The nodes one window drives: its own, and the ones it is paired with.

The window's own node runs in-process (``local.py``). Any other machine -- a
VM, a second desk -- runs the ordinary ``secdogie-node``, and the operator
pairs the window with it once:

  * on that machine, the window's App DID goes on the node's ``--apps`` and the
    operator DID on its ``--operators`` (``NodeHub.pairing_info``);
  * in the window, the operator pastes the node's ready line (the JSON line
    ``secdogie-node`` prints when it starts) and the machine's address
    (``HOST:PORT``, or just ``HOST`` when the ready line gives the port) --
    or, when the node is behind NAT, a rendezvous record instead; relay
    records add a fallback path. One item per line (``parse_pairing``).

Nothing pasted is taken on trust. The node's record must be self-signed by
the DID it names, and a rendezvous or relay record must be self-signed with
that role. The session then trusts exactly that one DID (``dialogue.connect``),
and a Gate 2 approval is signed for that node alone: the App checks every
challenge's subject against it.

``NodeBook`` keeps the paired nodes (public records only, no keys) in
``nodes.json`` (0600), re-verifying every record when it loads. ``NodeHub``
holds one ``DialogModel`` per node, so switching back and forth keeps each
conversation, and opens a paired node's session the first time it is chosen.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from secdogie_dialogue.app import short_did
from secdogie_dialogue.connect import NodeNotFound, open_session
from secdogie_dialogue.inspector import clean
from secdogie_dialogue.keystore import KeystoreError
from secdogie_identity import ALLOW_ANY, Allowlist
from secdogie_transport.membership import (
    DEVICE_HEADLESS,
    RECORD_TYPE,
    ROLE_RELAY,
    ROLE_RENDEZVOUS,
    verify_record,
)

from .model import DialogError, DialogModel

LOCAL = "local"
MAX_NAME = 40
_HOST = re.compile(r"[A-Za-z0-9.\-]+")
_HOST6 = re.compile(r"[0-9A-Fa-f:.]+")


class PairingError(ValueError):
    """Pasted text that does not describe a node the window can pair with."""


@dataclass(frozen=True)
class RemoteNode:
    name: str
    did: str
    record: dict  # the node's self-signed membership record
    rendezvous: tuple[dict, ...] = ()  # rendezvous' self-signed records: look the node up by DID
    relays: tuple[dict, ...] = ()  # relays' self-signed records: the fallback path
    addr: tuple[str, int] | None = None  # the address the operator gave, if any

    def address(self) -> tuple[str, int] | None:
        """Where to send first: the address given, else the record's best."""
        if self.addr is not None:
            return self.addr
        rec = verify_record(self.record, allowlist=Allowlist({self.did}))
        best = rec.endpoints.best() if rec is not None else None
        return (best.host, best.port) if best is not None else None

    def to_json(self) -> dict:
        return {"name": self.name, "did": self.did, "record": self.record, "rendezvous": list(self.rendezvous),
                "relays": list(self.relays), "addr": list(self.addr) if self.addr else None}


def _address(line: str, default_port: int | None) -> tuple[str, int]:
    """``HOST:PORT`` (an IPv6 host in brackets), or ``HOST`` alone when the
    ready line gave the port."""
    bad = PairingError(f"看不懂这一行：{clean(line, 60)}（地址写成 主机:端口）")
    if line.startswith("["):
        host, closed, rest = line[1:].partition("]")
        if not closed or (rest and not rest.startswith(":")) or not _HOST6.fullmatch(host):
            raise bad
        port = rest[1:]
    elif line.count(":") <= 1:
        host, _, port = line.partition(":")
        if not _HOST.fullmatch(host):
            raise bad
    else:
        raise bad  # a bare IPv6 address: its port cannot be told apart
    if port and not port.isdigit():
        raise bad
    number = int(port) if port else default_port
    if number is None:
        raise PairingError("地址要带端口，比如 10.0.0.5:7950。")
    if not 0 < number < 65536:
        raise PairingError("端口要在 1–65535 之间。")
    return host, number


def _items(text: str) -> tuple[list[dict], list[str]]:
    """The JSON records pasted, and the plain lines (addresses)."""
    records, plain = [], []
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("{"):
            plain.append(line)
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            raise PairingError(f"第 {n} 行不是完整的 JSON：每行粘贴一条记录。") from None
        if not isinstance(obj, dict):
            raise PairingError(f"第 {n} 行不是一条记录。")
        records.append(obj)
    return records, plain


def _check(record: dict, did: str, rendezvous, relays) -> None:
    """Every record must verify: the node's by its own DID, a service's with
    its role. Raises ``PairingError`` naming what is wrong."""
    rec = verify_record(record, allowlist=Allowlist({did}))
    if rec is None:
        raise PairingError("节点的记录没有通过签名校验（或不是它自己签的）。")
    if ROLE_RELAY in rec.roles or ROLE_RENDEZVOUS in rec.roles:
        raise PairingError("这是中继 / rendezvous 的记录，不是节点的：请粘贴节点启动时打印的 ready 行。")
    if rec.device_class == DEVICE_HEADLESS:
        raise PairingError("这是一个 headless 节点：它不接受目标，没法从这里操作。")
    for obj in rendezvous:
        r = verify_record(obj, allowlist=ALLOW_ANY)
        if r is None or ROLE_RENDEZVOUS not in r.roles:
            raise PairingError("一条 rendezvous 记录没有通过校验。")
    for obj in relays:
        r = verify_record(obj, allowlist=ALLOW_ANY)
        if r is None or ROLE_RELAY not in r.roles:
            raise PairingError("一条中继记录没有通过校验。")


def parse_pairing(text: str, *, name: str = "", refuse=()) -> RemoteNode:
    """A paired node from pasted text: the node's ready line (or its bare
    record), its address, and any rendezvous / relay records, one per line.
    ``refuse``: DIDs that cannot be paired (this window's own)."""
    node_record, did, listen_port = None, None, None
    rendezvous, relays = [], []
    records, plain = _items(text)
    for obj in records:
        if obj.get("event") == "ready" and isinstance(obj.get("record"), dict):
            if node_record is not None:
                raise PairingError("粘贴了不止一个节点：一次配对一个。")
            node_record, did = obj["record"], str(obj.get("did") or "")
            port = str(obj.get("listen") or "").rpartition(":")[2]
            listen_port = int(port) if port.isdigit() else None
            continue
        if obj.get("type") != RECORD_TYPE:
            raise PairingError("有一行既不是 ready 行，也不是成员记录。")
        roles = obj.get("roles") if isinstance(obj.get("roles"), list) else []
        if ROLE_RENDEZVOUS in roles or ROLE_RELAY in roles:
            if ROLE_RENDEZVOUS in roles:
                rendezvous.append(obj)
            if ROLE_RELAY in roles:
                relays.append(obj)
            continue
        if node_record is not None:
            raise PairingError("粘贴了不止一个节点：一次配对一个。")
        node_record, did = obj, str(obj.get("did") or "")
    if node_record is None:
        raise PairingError("没有找到节点的 ready 行。")
    if not did or node_record.get("did") != did:
        raise PairingError("ready 行里的 DID 和它的记录对不上。")
    if did in set(refuse):
        raise PairingError("这是本窗口自己的 DID，不能和自己配对。")
    _check(node_record, did, rendezvous, relays)
    if len(plain) > 1:
        raise PairingError("地址只要一行。")
    addr = _address(plain[0], listen_port) if plain else None
    node = RemoteNode(clean(name.strip(), MAX_NAME) or short_did(did), did, node_record, tuple(rendezvous),
                      tuple(relays), addr)
    if node.address() is None and not rendezvous and not relays:
        raise PairingError("还不知道这个节点在哪：请加一行它的地址（如 10.0.0.5:7950），或附上 rendezvous 记录。")
    return node


class NodeBook:
    """The paired nodes, kept in ``path`` (0600): public records only."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._nodes: dict[str, RemoteNode] = {}
        for entry in self._read():
            try:
                did = str(entry["did"])
                rendezvous, relays = tuple(entry.get("rendezvous") or ()), tuple(entry.get("relays") or ())
                _check(entry["record"], did, rendezvous, relays)
            except (KeyError, TypeError, PairingError):
                continue  # an entry that no longer verifies is dropped, not trusted
            name = clean(str(entry.get("name") or ""), MAX_NAME) or short_did(did)
            addr = entry.get("addr")
            try:
                addr = (str(addr[0]), int(addr[1])) if addr else None
            except (TypeError, ValueError, IndexError):
                continue
            self._nodes[did] = RemoteNode(name, did, entry["record"], rendezvous, relays, addr)

    def _read(self) -> list:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [e for e in data.get("nodes", []) if isinstance(e, dict)] if isinstance(data, dict) else []

    def _write(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"nodes": [n.to_json() for n in self._nodes.values()]}, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def nodes(self) -> list[RemoteNode]:
        with self._lock:
            return list(self._nodes.values())

    def get(self, did: str) -> RemoteNode | None:
        with self._lock:
            return self._nodes.get(did)

    def add(self, node: RemoteNode) -> None:
        with self._lock:
            self._nodes[node.did] = node
            self._write()

    def remove(self, did: str) -> None:
        with self._lock:
            if self._nodes.pop(did, None) is not None:
                self._write()


class NodeHub:
    """The window's nodes: ``LOCAL`` and the paired ones, one ``DialogModel``
    each. ``current`` is the one the window shows."""

    def __init__(self, local_controller, operator_key, app_identity, book: NodeBook, *, api_keys=None,
                 opener=open_session, listen: tuple[str, int] = ("0.0.0.0", 0), clock=time.time):
        self.key = operator_key
        self.app_identity = app_identity
        self.book = book
        self._opener, self._listen, self._clock = opener, listen, clock
        self._models: dict[str, DialogModel] = {
            LOCAL: DialogModel(local_controller, operator_key, api_keys, label="本机节点", clock=clock)}
        self._links: dict = {}
        self._local_did = getattr(local_controller, "peer_did", "")
        self.current = LOCAL

    @property
    def model(self) -> DialogModel:
        """The node shown now."""
        return self._models[self.current]

    @property
    def local(self) -> DialogModel:
        """This machine's node (it also keeps the model's API key)."""
        return self._models[LOCAL]

    def refresh_others(self) -> None:
        """Catch up the nodes not shown (their approvals still expire)."""
        for key, model in list(self._models.items()):
            if key != self.current:
                model.refresh()

    def choices(self) -> list[tuple[str, str]]:
        """(key, name) for the switcher: this machine first, then the paired nodes."""
        return [(LOCAL, "本机")] + [(n.did, n.name) for n in self.book.nodes()]

    def name(self, key: str) -> str:
        return dict(self.choices()).get(key, short_did(key))

    def switch(self, key: str) -> DialogModel:
        """Show node ``key``, opening its session the first time."""
        if key != LOCAL and key not in self._models:
            node = self.book.get(key)
            if node is None:
                raise DialogError("没有这个节点。")
            try:
                link = self._opener(self.app_identity, node.did, listen=self._listen, node_addr=node.address(),
                                    rendezvous=node.rendezvous, relays=node.relays)
            except NodeNotFound as e:
                raise DialogError(f"找不到这个节点：{e}") from None
            except (OSError, ValueError) as e:
                raise DialogError(f"连不上这个节点：{e}") from None
            self._links[key] = link
            self._models[key] = DialogModel(link.controller, self.key, None, label=f"节点「{node.name}」",
                                            clock=self._clock)
        self.current = key
        return self.model

    def pair(self, text: str, name: str = "") -> RemoteNode:
        """Pair with the node the pasted text describes, and switch to it."""
        try:
            node = parse_pairing(text, name=name, refuse={self._local_did, self.app_identity.did})
        except PairingError as e:
            raise DialogError(str(e)) from None
        self.forget(node.did)  # pasted again: the new records replace the old session
        self.book.add(node)
        self.switch(node.did)
        return node

    def forget(self, did: str) -> None:
        """Unpair ``did``: close its session and drop it from the book."""
        link = self._links.pop(did, None)
        self._models.pop(did, None)
        if link is not None:
            link.close()
        self.book.remove(did)
        if self.current == did:
            self.current = LOCAL

    def waiting_elsewhere(self) -> int:
        """Cards waiting on nodes other than the one shown."""
        return sum(m.waiting() for k, m in self._models.items() if k != self.current)

    def pairing_info(self) -> tuple[str, str | None]:
        """What the other machine's node must trust: this window's App DID (its
        ``--apps``) and the operator DID (its ``--operators``; None until the
        passphrase is set)."""
        return self.app_identity.did, self.key.did

    def set_passphrase(self, passphrase: str, again: str) -> str:
        """Set the passphrase now (the other node needs the operator DID before
        any approval): make and seal the operator key. Returns its DID."""
        try:
            return self.key.create(passphrase, again).did
        except KeystoreError as e:
            raise DialogError(str(e)) from None

    def close(self) -> None:
        for link in list(self._links.values()):
            try:
                link.close()
            except Exception:  # noqa: BLE001 - close the rest
                pass
        self._links.clear()


__all__ = ["LOCAL", "NodeBook", "NodeHub", "PairingError", "RemoteNode", "parse_pairing"]
