"""The browser link's record layer: libsodium's crypto_kx, AES-256-GCM
records with a strict sequence, and keys that are wiped, not just dropped."""
from __future__ import annotations

import ast
import pathlib
import threading

import pytest

pytest.importorskip("nacl")
pytest.importorskip("cryptography")

import nacl.bindings as b  # noqa: E402
from secdogie_transport.linkcipher import (  # noqa: E402
    MAX_PLAINTEXT,
    MAX_RECORD,
    KxKeypair,
    LinkCipherError,
    RecordCipher,
    SessionKeys,
)

LINK = "ab" * 32


def _pair():
    c, s = KxKeypair.generate(), KxKeypair.generate()
    ck = SessionKeys.derive("client", c, s.public)
    sk = SessionKeys.derive("server", s, c.public)
    c.zeroize()
    s.zeroize()
    return RecordCipher(ck, link=LINK), RecordCipher(sk, link=LINK), ck, sk


def test_crypto_kx_matches_pynacl_and_the_roles_mirror():
    cpk, csk = b.crypto_kx_seed_keypair(b"\x01" * 32)
    spk, ssk = b.crypto_kx_seed_keypair(b"\x02" * 32)
    client = KxKeypair.from_secret(csk)
    assert client.public == cpk
    assert SessionKeys.derive("client", client, spk).raw() == b.crypto_kx_client_session_keys(cpk, csk, spk)
    assert SessionKeys.derive("server", KxKeypair.from_secret(ssk), cpk).raw() == \
        b.crypto_kx_server_session_keys(spk, ssk, cpk)
    crx, ctx = SessionKeys.derive("client", client, spk).raw()
    srx, stx = SessionKeys.derive("server", KxKeypair.from_secret(ssk), cpk).raw()
    assert (crx, ctx) == (stx, srx)


def test_a_low_order_peer_key_is_refused():
    with pytest.raises(LinkCipherError):
        SessionKeys.derive("client", KxKeypair.generate(), b"\x00" * 32)


def test_records_both_ways():
    a, z, _, _ = _pair()
    for i in range(5):
        rec = a.seal(f"to node {i}".encode())
        assert rec[:1] == b"S" and int.from_bytes(rec[1:9], "big") == i
        assert z.open(rec) == f"to node {i}".encode()
    assert a.open(z.seal(b"to page")) == b"to page"


def test_the_limits_are_checked_before_a_seq_is_spent():
    a, z, _, _ = _pair()
    with pytest.raises(LinkCipherError):
        a.seal(b"x" * (MAX_PLAINTEXT + 1))
    assert not a.closed
    rec = a.seal(b"x" * MAX_PLAINTEXT)
    assert len(rec) == MAX_RECORD and int.from_bytes(rec[1:9], "big") == 0
    assert z.open(rec) == b"x" * MAX_PLAINTEXT


@pytest.mark.parametrize("mutate", [
    lambda r0, r1: r1,                                   # a gap (record 0 never arrived)
    lambda r0, r1: r0[:-1] + bytes([r0[-1] ^ 1]),        # a flipped tag bit
    lambda r0, r1: r0[:12] + bytes([r0[12] ^ 1]) + r0[13:],  # a flipped ciphertext bit
    lambda r0, r1: b"T" + r0[1:],                        # not a record
    lambda r0, r1: r0[:20],                              # too short
    lambda r0, r1: r0 + b"\x00" * (MAX_RECORD + 1 - len(r0)),  # too long
])
def test_any_bad_record_closes_the_receiver_for_good(mutate):
    a, z, _, zk = _pair()
    r0, r1 = a.seal(b"zero"), a.seal(b"one")
    with pytest.raises(LinkCipherError):
        z.open(mutate(r0, r1))
    assert z.closed and zk.wiped
    with pytest.raises(LinkCipherError):
        z.open(r0)  # even the honest record: a closed cipher stays closed
    with pytest.raises(LinkCipherError):
        z.seal(b"anything")


def test_a_replay_and_another_link_are_refused():
    a, z, ak, _ = _pair()
    r0 = a.seal(b"zero")
    assert z.open(r0) == b"zero"
    with pytest.raises(LinkCipherError):
        z.open(r0)
    a, z, ak, _ = _pair()
    elsewhere = RecordCipher(SessionKeys(*_raw_copy(ak)), link="cd" * 32)  # the very keys, another link
    with pytest.raises(LinkCipherError):
        z.open(elsewhere.seal(b"same keys, another link"))


def _raw_copy(keys: SessionKeys):
    from nacl._sodium import ffi

    rx, tx = keys.raw()
    out = []
    for raw in (rx, tx):
        buf = ffi.new("unsigned char[]", 32)
        ffi.memmove(buf, raw, 32)
        out.append(buf)
    return out


def test_zeroize_wipes_the_buffers():
    from nacl._sodium import ffi

    a, _, ak, _ = _pair()
    rx, tx = ak._rx, ak._tx
    kp = KxKeypair.generate()
    secret = kp._secret
    kp.zeroize()
    assert ffi.buffer(secret)[:] == b"\x00" * 32 and kp.wiped
    a.zeroize("the link went away")
    assert ffi.buffer(rx)[:] == b"\x00" * 32 and ffi.buffer(tx)[:] == b"\x00" * 32
    assert a.closed and a.closed_reason == "the link went away"
    with pytest.raises(LinkCipherError):
        a.seal(b"after")
    with pytest.raises(LinkCipherError):
        SessionKeys.derive("client", kp, KxKeypair.generate().public)


def test_a_dropped_send_closes_the_cipher_and_concurrent_sends_stay_in_order():
    a, z, _, _ = _pair()
    with pytest.raises(LinkCipherError):
        a.seal_and_send(b"lost", lambda rec: False)
    assert a.closed
    a, z, _, _ = _pair()
    wire = []

    def worker(n):
        for i in range(200):
            a.seal_and_send(f"{n}-{i}".encode(), lambda rec: wire.append(rec) or True)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [int.from_bytes(r[1:9], "big") for r in wire] == list(range(800))
    assert len({z.open(r) for r in wire}) == 800


def test_no_production_code_makes_keys_from_a_seed():
    root = pathlib.Path(__file__).resolve().parents[2]
    for pkg in ("transport/secdogie_transport", "node/secdogie_node", "dialogue/secdogie_dialogue",
                "identity/secdogie_identity"):
        for path in (root / pkg).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "") for n in ast.walk(tree)}
            assert "crypto_kx_seed_keypair" not in names, path
