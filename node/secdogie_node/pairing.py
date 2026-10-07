"""``secdogie-node pair``: let one browser in, once, with the owner watching.

The only step of the browser link that needs a person. It runs in the
foreground on the node's own terminal, beside (or without) the resident node:

  1. a one-time offer -- a 32-byte secret, valid ``ttl`` seconds, single use,
     burned after five failed attempts -- becomes a link that is written to the
     controlling terminal only (never stdout, never a log): the link is a key;
  2. the page that opens it meets this process in a room derived from the
     secret (never the resident node's room), checks this node's W1 statement
     against the DID in the link, and sends its signed, HMAC'd hello;
  3. the terminal shows the 12-digit check code and asks whether the page shows
     the same digits -- and, when the page offers an operator key, whether that
     browser may also approve steps that cannot be undone; the page shows the
     same digits and waits for one tap;
  4. only with the owner's "y" *and* the page's tap does the node enroll the
     App (and, if allowed, the operator key) by appending to the ``--apps`` /
     ``--operators`` files, and answer with a signed receipt naming the
     resident node's room. A running node picks the new lines up by itself
     (``AllowlistWatcher``) and records the enrollment in its journal.

Nothing here installs anything or keeps running: it exits when the pairing is
done, refused, or expired.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from secdogie_identity import append_authorized
from secdogie_identity import linkauth as la

log = logging.getLogger("secdogie_node.pairing")

DEFAULT_TTL = 600
MAX_TTL = 3600
MAX_FAILURES = 5
MIN_INTERVAL = 2.0

Q_CODE = "浏览器上显示的是 {code} 吗？ Does the browser show {code}? [y/N] "
Q_OPERATOR = ("也允许这个浏览器批准做了就撤不回的操作吗？ "
              "May this browser also approve steps that cannot be undone? [y/N] ")


@dataclass
class PairingOffer:
    """One pairing: single use, short-lived, burned after repeated failures."""

    secret: bytes = field(default_factory=lambda: secrets.token_bytes(la.SECRET_BYTES))
    ttl: float = DEFAULT_TTL
    clock: Callable[[], float] = time.time
    expires_at: int = 0
    used: bool = False
    failures: int = 0
    burned: bool = False
    _last_attempt: float = 0.0

    def __post_init__(self):
        if not 0 < self.ttl <= MAX_TTL:
            raise ValueError(f"a pairing lasts between 1 and {MAX_TTL} seconds")
        if not self.expires_at:
            self.expires_at = int(self.clock() + self.ttl)

    @property
    def pairing_id(self) -> str:
        return la.pairing_id(self.secret)

    @property
    def room(self) -> str:
        return la.pairing_room(self.secret)

    def usable(self) -> bool:
        return not (self.used or self.burned) and self.clock() < self.expires_at

    def attempt(self) -> bool:
        """Rate-limit attempts (one every two seconds)."""
        now = self.clock()
        if now - self._last_attempt < MIN_INTERVAL:
            return False
        self._last_attempt = now
        return True

    def fail(self) -> None:
        self.failures += 1
        if self.failures >= MAX_FAILURES:
            self.burned = True

    def link(self, ui: str, node_did: str) -> str:
        return la.pairing_link(ui, node_did=node_did, secret=self.secret, expires_at=self.expires_at)


# ---- the terminal ---------------------------------------------------------------------------


class Terminal:
    """The controlling terminal, opened directly: what is written here is never
    on stdout or in a log. Refuses (raises OSError) when there is none."""

    def __init__(self, path: str = "/dev/tty"):
        # Two handles: a terminal is not seekable, so one read-write text file cannot be opened on it.
        self._in = open(path, encoding="utf-8")  # noqa: SIM115 - held for the session
        try:
            self._out = open(path, "w", encoding="utf-8")  # noqa: SIM115
        except OSError:
            self._in.close()
            raise
        self._lock = threading.Lock()

    def say(self, text: str) -> None:
        with self._lock:
            self._out.write(text + "\n")
            self._out.flush()

    def ask(self, question: str) -> bool:
        with self._lock:
            self._out.write(question)
            self._out.flush()
            answer = self._in.readline()
        return answer.strip().lower() in ("y", "yes", "是")

    def close(self) -> None:
        for f in (self._in, self._out):
            try:
                f.close()
            except OSError:
                pass


# ---- the policy on the pairing link ------------------------------------------------------------


@dataclass
class Enrollment:
    app_did: str
    operator_did: str | None


class PairingPolicy:
    """The node's side of one pairing link (see the module docstring).
    ``ask(question) -> bool`` is the owner's terminal; ``enroll(app, operator)``
    writes the allowlists and may raise, which refuses the pairing."""

    def __init__(self, identity, offer: PairingOffer, *, standing_room: str, ask: Callable[[str], bool],
                 enroll: Callable[[str, str | None], None], say: Callable[[str], None] = lambda s: None,
                 clock=time.time):
        self.identity = identity
        self.offer = offer
        self.standing_room = standing_room
        self.ask = ask
        self.enroll = enroll
        self.say = say
        self._clock = clock
        self._lock = threading.Lock()
        self.done = threading.Event()
        self.result: Enrollment | None = None
        self.refused: str | None = None
        self._hello: dict | None = None
        self._code: str | None = None
        self._owner: tuple[bool, bool] | None = None  # (it is the right page, operator allowed)
        self._confirm = None
        self._link = None
        self._ended = False  # refused or enrolled: nothing more happens on this offer

    # -- LinkPolicy --------------------------------------------------------------------

    def on_open(self, link) -> None:
        link.extend(max(1.0, self.offer.expires_at - self._clock()))
        link.send_text(la.create_link_binding(self.identity, room=link.room, local=link.local_fingerprints,
                                              remote=link.remote_fingerprints, clock=self._clock))

    def on_text(self, link, msg: dict) -> None:
        t = msg.get("type")
        if t == la.PAIR_HELLO_TYPE:
            self._on_hello(link, msg)
        elif t == la.PAIR_CONFIRM_TYPE:
            self._on_confirm(link, msg)
        else:
            link.close("not a pairing statement")

    # -- the steps -----------------------------------------------------------------------

    def _refuse(self, link, reason: str, why: str) -> None:
        with self._lock:
            if self._ended:
                return
            self._ended = True
            self.refused = why
        link.send_text(la.create_refusal(self.identity, reason=reason, local=link.local_fingerprints,
                                         remote=link.remote_fingerprints, pairing=self.offer.pairing_id,
                                         clock=self._clock))
        link.close(why, delay=0.5)
        self.say(f"pairing refused: {why}")
        self.done.set()

    def _on_hello(self, link, msg: dict) -> None:
        if self._hello is not None:
            link.close("a second hello")
            return
        if not self.offer.usable() or not self.offer.attempt():
            self._refuse(link, "pairing-unavailable", "the offer is used, expired or busy")
            return
        r = la.verify_pair_hello(msg, secret=self.offer.secret, node_did=self.identity.did,
                                 observed_local=link.local_fingerprints, observed_remote=link.remote_fingerprints,
                                 clock=self._clock)
        if not r.ok:
            if r.mac_failed:
                self.offer.fail()
            log.warning("a pairing hello did not verify: %s", r.reason)
            link.close(r.reason or "bad hello")
            return
        self._hello, self._code, self._link = msg, r.code, link
        threading.Thread(target=self._ask_owner, args=(link, r), daemon=True, name="pairing-ask").start()

    def _ask_owner(self, link, hello: la.HelloResult) -> None:
        try:
            right = self.ask(Q_CODE.format(code=hello.code))
            operator = bool(right and hello.operator_did and self.ask(Q_OPERATOR))
        except Exception:  # noqa: BLE001 - no answer is a no
            right, operator = False, False
        if not right:
            self.offer.burned = True  # a "no" ends this offer: whoever holds the link starts over
            self._refuse(link, "pairing-rejected", "the owner said no")
            return
        with self._lock:
            self._owner = (True, operator)
        self.say("等浏览器上点「连接」…  Waiting for the tap in the browser…")
        self._maybe_finish(link)

    def _on_confirm(self, link, msg: dict) -> None:
        if self._hello is None or self._confirm is not None:
            link.close("a confirmation out of turn")
            return
        r = la.verify_pair_confirm(msg, secret=self.offer.secret, node_did=self.identity.did, hello=self._hello,
                                   clock=self._clock)
        if not r.ok:
            if r.mac_failed:
                self.offer.fail()
            self._refuse(link, "pairing-rejected", f"the confirmation did not verify: {r.reason}")
            return
        with self._lock:
            self._confirm = r
        self._maybe_finish(link)

    def _maybe_finish(self, link) -> None:
        with self._lock:
            if self._ended or self._owner is None or self._confirm is None:
                return
            usable = self.offer.usable()
            if usable:
                self.offer.used = True
            app = self._hello["did"]
            operator = self._confirm.operator_did if self._owner[1] else None
        if not usable:
            self._refuse(link, "pairing-unavailable", "the offer expired")
            return
        with self._lock:
            if self._ended:
                return
            self._ended = True
        try:
            self.enroll(app, operator)
        except Exception as e:  # noqa: BLE001 - nothing half-done is reported as done
            log.error("enrollment failed: %s", e)
            self._ended = False
            self._refuse(link, "pairing-rejected", f"could not record the enrollment: {e}")
            return
        receipt = la.create_paired(self.identity, app_did=app, operator_did=operator or "", room=self.standing_room,
                                   pairing=self.offer.pairing_id, local=link.local_fingerprints,
                                   remote=link.remote_fingerprints, clock=self._clock)
        link.send_text(receipt)
        link.close("paired", delay=1.0)
        self.result = Enrollment(app, operator)
        self.done.set()
        self.say("已配对。 Paired." + ("（这个浏览器也可以批准不可撤回的步骤）" if operator else ""))


def file_enroller(apps_path, operators_path, *, pairing_id: str) -> Callable[[str, str | None], None]:
    """Append the App (and operator) to the allowlist files -- durably, before
    the receipt goes out."""
    if os.path.realpath(apps_path) == os.path.realpath(operators_path):
        raise ValueError("--apps and --operators must be two files: an App key is never an operator key")

    def enroll(app: str, operator: str | None) -> None:
        from secdogie_identity import Allowlist

        ops = Allowlist.load(operators_path) if os.path.exists(operators_path) else Allowlist()
        apps = Allowlist.load(apps_path) if os.path.exists(apps_path) else Allowlist()
        if ops.contains(app) or (operator is not None and apps.contains(operator)):
            raise ValueError("an App key cannot also be an operator key")
        label = f"paired via webrtc {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} pairing={pairing_id[:8]}"
        append_authorized(apps_path, app, label=label)
        if operator is not None:
            append_authorized(operators_path, operator, label=label)

    return enroll


__all__ = ["PairingOffer", "PairingPolicy", "Terminal", "Enrollment", "file_enroller", "DEFAULT_TTL"]
