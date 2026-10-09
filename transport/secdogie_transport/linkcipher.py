"""The record layer of the browser link (``secdogie/symbiont-link/v2``).

After W1 and a signed handshake (``secdogie_identity.linkauth``), both ends
run libsodium's ``crypto_kx`` -- the page as client, the node as server -- and
every data-channel message is one record::

    'S' | seq (u64, big-endian) | AES-256-GCM(plaintext) with a 128-bit tag

    nonce = 4 zero bytes || seq
    AAD   = b"secdogie/symbiont-link/v2" || link (32 bytes) || 'S' || seq

Each direction has its own key, and its seq starts at 0 and goes up by exactly
one per record; anything else (a gap, a replay, a forgery, a record that is too
long) closes the cipher for good. AES-256-GCM because it is the AEAD a browser
has natively (WebCrypto); the keys are fresh for every link.

This is defense in depth *inside* DTLS -- W1 is what keeps the signaling
gateway out -- and it has not been audited. It does no padding, shaping or
disguise of any kind.

Key material: ``crypto_kx`` writes the session keys straight into buffers this
module owns, and ``zeroize()`` wipes them with libsodium's ``sodium_memzero``.
Python cannot promise more than that: ``cryptography``'s ``AESGCM`` keeps its
own copy of each key (inside OpenSSL), which is released -- not wiped -- when
the cipher is dropped.
"""
from __future__ import annotations

import threading
from collections.abc import Callable

import nacl.bindings  # noqa: F401 - runs sodium_init() before the raw library is touched
from nacl._sodium import ffi, lib

PROTOCOL = b"secdogie/symbiont-link/v2"
RECORD_TAG = b"S"
HEADER_BYTES = 9
TAG_BYTES = 16
KEY_BYTES = 32
MAX_RECORD = 65536                                  # bytes on the wire, header and tag included
MAX_PLAINTEXT = MAX_RECORD - HEADER_BYTES - TAG_BYTES  # 65511
MAX_RECORDS = 2**32                                 # per direction; then the link closes


class LinkCipherError(Exception):
    """The handshake or a record failed. The cipher is closed when this is
    raised by ``seal`` or ``open``."""


def _buffer(n: int):
    return ffi.new("unsigned char[]", n)


def _wipe(buf) -> None:
    if buf is not None:
        lib.sodium_memzero(buf, len(buf))


class KxKeypair:
    """One side's ephemeral crypto_kx key pair. New for every handshake; wipe
    it (``zeroize``) as soon as the session keys exist."""

    def __init__(self, public: bytes, secret_buf):
        self.public = public
        self._secret = secret_buf

    @classmethod
    def generate(cls) -> KxKeypair:
        pk, sk = _buffer(KEY_BYTES), _buffer(KEY_BYTES)
        if lib.crypto_kx_keypair(pk, sk) != 0:
            raise LinkCipherError("crypto_kx_keypair failed")
        return cls(ffi.buffer(pk, KEY_BYTES)[:], sk)

    @classmethod
    def from_secret(cls, secret: bytes) -> KxKeypair:
        """A key pair from a known secret key -- for test vectors only."""
        if len(secret) != KEY_BYTES:
            raise ValueError("a crypto_kx secret key is 32 bytes")
        sk = _buffer(KEY_BYTES)
        ffi.memmove(sk, secret, KEY_BYTES)
        pk = _buffer(KEY_BYTES)
        if lib.crypto_scalarmult_base(pk, sk) != 0:
            raise LinkCipherError("crypto_scalarmult_base failed")
        return cls(ffi.buffer(pk, KEY_BYTES)[:], sk)

    @property
    def wiped(self) -> bool:
        return self._secret is None

    def zeroize(self) -> None:
        _wipe(self._secret)
        self._secret = None


class SessionKeys:
    """The two directional keys of one link, in buffers that ``zeroize`` wipes."""

    def __init__(self, rx_buf, tx_buf):
        self._rx, self._tx = rx_buf, tx_buf

    @classmethod
    def derive(cls, role: str, own: KxKeypair, peer_public: bytes) -> SessionKeys:
        """``role`` is "client" (the page) or "server" (the node). Raises
        LinkCipherError for a peer key libsodium refuses (an all-zero shared
        secret, i.e. a low-order point)."""
        if own.wiped:
            raise LinkCipherError("the key pair was already wiped")
        if not isinstance(peer_public, (bytes, bytearray)) or len(peer_public) != KEY_BYTES:
            raise LinkCipherError("a crypto_kx public key is 32 bytes")
        rx, tx = _buffer(KEY_BYTES), _buffer(KEY_BYTES)
        own_pk = ffi.from_buffer(own.public)
        peer_pk = ffi.from_buffer(bytes(peer_public))
        if role == "client":
            rc = lib.crypto_kx_client_session_keys(rx, tx, own_pk, own._secret, peer_pk)
        elif role == "server":
            rc = lib.crypto_kx_server_session_keys(rx, tx, own_pk, own._secret, peer_pk)
        else:
            raise ValueError("role is client or server")
        if rc != 0:
            _wipe(rx)
            _wipe(tx)
            raise LinkCipherError("the peer's key was refused")
        return cls(rx, tx)

    def raw(self) -> tuple[bytes, bytes]:
        """(rx, tx) as bytes -- for test vectors only."""
        if self._rx is None:
            raise LinkCipherError("the session keys were wiped")
        return ffi.buffer(self._rx, KEY_BYTES)[:], ffi.buffer(self._tx, KEY_BYTES)[:]

    @property
    def wiped(self) -> bool:
        return self._rx is None

    def zeroize(self) -> None:
        _wipe(self._rx)
        _wipe(self._tx)
        self._rx = self._tx = None


def _header(seq: int) -> bytes:
    return RECORD_TAG + seq.to_bytes(8, "big")


def _nonce(seq: int) -> bytes:
    return b"\x00\x00\x00\x00" + seq.to_bytes(8, "big")


class RecordCipher:
    """Seals and opens the records of one link. Thread-safe: ``seal_and_send``
    takes the next seq and hands the record to the transport under one lock, so
    the order on the wire is the order of the seqs."""

    def __init__(self, keys: SessionKeys, *, link: str):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if keys.wiped:
            raise LinkCipherError("the session keys were already wiped")
        link_bytes = bytes.fromhex(link)
        if len(link_bytes) != 32:
            raise ValueError("a link digest is 32 bytes")
        self._keys = keys
        self._link = link_bytes
        self._seal = AESGCM(ffi.buffer(keys._tx, KEY_BYTES)[:])
        self._open = AESGCM(ffi.buffer(keys._rx, KEY_BYTES)[:])
        self._send_seq = 0
        self._recv_seq = 0
        self._lock = threading.Lock()
        self.closed_reason: str | None = None

    @property
    def closed(self) -> bool:
        return self.closed_reason is not None

    def _close(self, reason: str) -> None:
        if self.closed_reason is None:
            self.closed_reason = reason
        self._seal = self._open = None
        self._keys.zeroize()

    def zeroize(self, reason: str = "closed") -> None:
        with self._lock:
            self._close(reason)

    def _aad(self, header: bytes) -> bytes:
        return PROTOCOL + self._link + header

    def seal(self, plaintext: bytes) -> bytes:
        with self._lock:
            return self._seal_locked(plaintext)

    def _seal_locked(self, plaintext: bytes) -> bytes:
        if self.closed:
            raise LinkCipherError(f"the cipher is closed ({self.closed_reason})")
        if not isinstance(plaintext, (bytes, bytearray)) or len(plaintext) > MAX_PLAINTEXT:
            raise LinkCipherError("a record holds at most 65511 bytes")
        if self._send_seq >= MAX_RECORDS:
            self._close("too many records")
            raise LinkCipherError("too many records on one link")
        seq = self._send_seq
        header = _header(seq)
        record = header + self._seal.encrypt(_nonce(seq), bytes(plaintext), self._aad(header))
        self._send_seq = seq + 1
        return record

    def seal_and_send(self, plaintext: bytes, send: Callable[[bytes], bool]) -> None:
        """Seal, then ``send(record)``, under one lock. A send that reports
        failure closes the cipher: a record that never left would leave a gap
        the peer must treat as an attack."""
        with self._lock:
            record = self._seal_locked(plaintext)
            if not send(record):
                self._close("the transport dropped a record")
                raise LinkCipherError("the transport dropped a record")

    def open(self, record: bytes) -> bytes:
        with self._lock:
            if self.closed:
                raise LinkCipherError(f"the cipher is closed ({self.closed_reason})")
            if not isinstance(record, (bytes, bytearray)) or not (HEADER_BYTES + TAG_BYTES <= len(record) <= MAX_RECORD):
                self._close("a record of the wrong size")
                raise LinkCipherError("a record of the wrong size")
            record = bytes(record)
            if record[:1] != RECORD_TAG:
                self._close("not a record")
                raise LinkCipherError("not a record")
            seq = int.from_bytes(record[1:HEADER_BYTES], "big")
            if seq != self._recv_seq:
                self._close("out of sequence")
                raise LinkCipherError("a record out of sequence")
            header = record[:HEADER_BYTES]
            try:
                plaintext = self._open.decrypt(_nonce(seq), record[HEADER_BYTES:], self._aad(header))
            except Exception as e:  # noqa: BLE001 - InvalidTag, or anything else: never half-open
                self._close("a record failed to authenticate")
                raise LinkCipherError("a record failed to authenticate") from e
            self._recv_seq = seq + 1
            return plaintext


__all__ = ["PROTOCOL", "MAX_RECORD", "MAX_PLAINTEXT", "MAX_RECORDS", "HEADER_BYTES", "TAG_BYTES",
           "LinkCipherError", "KxKeypair", "SessionKeys", "RecordCipher"]
