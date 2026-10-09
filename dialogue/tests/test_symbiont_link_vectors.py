"""fixtures/vectors/symbiont_link.json from the Python side: the handshake,
records and packets the operator page must reproduce verify and decode with
the production modules (the freshness check is in test_cross_language_vectors)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from secdogie_dialogue import symbiont_packets as sp  # noqa: E402
from secdogie_identity import linkauth as la  # noqa: E402
from secdogie_transport.linkcipher import (  # noqa: E402
    KxKeypair,
    LinkCipherError,
    RecordCipher,
    SessionKeys,
)

V = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "vectors" / "symbiont_link.json")
               .read_text(encoding="utf-8"))


def test_blake2b():
    for case in V["blake2b_512"]:
        assert hashlib.blake2b(bytes.fromhex(case["input_hex"]), digest_size=64).hexdigest() == case["digest_hex"]


def test_kx_and_records():
    kx, rec, hs = V["kx"], V["records"], V["handshake"]
    page = SessionKeys.derive("client", KxKeypair.from_secret(bytes.fromhex(kx["client_secret_hex"])),
                              bytes.fromhex(kx["server_public_hex"]))
    assert page.raw() == (bytes.fromhex(kx["client_rx_hex"]), bytes.fromhex(kx["client_tx_hex"]))
    node = SessionKeys.derive("server", KxKeypair.from_secret(bytes.fromhex(kx["server_secret_hex"])),
                              bytes.fromhex(kx["client_public_hex"]))
    page_c, node_c = RecordCipher(page, link=hs["link"]), RecordCipher(node, link=hs["link"])
    for case in rec["page_to_node"]:
        assert page_c.seal(bytes.fromhex(case["plaintext_hex"])).hex() == case["record_hex"]
        assert node_c.open(bytes.fromhex(case["record_hex"])).hex() == case["plaintext_hex"]
    for case in rec["node_to_page"]:
        assert page_c.open(bytes.fromhex(case["record_hex"])).hex() == case["plaintext_hex"]
    for bad in kx["refused_peer_public_hex"]:
        with pytest.raises(LinkCipherError):
            SessionKeys.derive("client", KxKeypair.generate(), bytes.fromhex(bad))


def test_record_rejects():
    kx, hs = V["kx"], V["handshake"]
    for case in V["records"]["rejects"]:
        node = RecordCipher(SessionKeys.derive("server", KxKeypair.from_secret(bytes.fromhex(kx["server_secret_hex"])),
                                               bytes.fromhex(kx["client_public_hex"])), link=hs["link"])
        *ok, last = case["records_hex"]
        for r in ok:
            node.open(bytes.fromhex(r))
        with pytest.raises(LinkCipherError):
            node.open(bytes.fromhex(last))


def test_handshake():
    hs = V["handshake"]
    nb, pb = json.loads(hs["node_binding"]), json.loads(hs["page_binding"])
    assert la.link_digest(nb, pb) == hs["link"]
    init = json.loads(hs["init"])
    r = la.verify_handshake_init(init, app_did=hs["app"]["did"], link=hs["link"], now_ms=hs["now_ms"])
    assert r.ok and r.pubkey.hex() == V["kx"]["client_public_hex"]
    acc = la.verify_handshake_accept(json.loads(hs["accept"]), node_did=hs["node"]["did"], client_pubkey=r.pubkey,
                                     link=hs["link"], now_ms=hs["now_ms"])
    assert acc.ok
    for case in hs["init_rejects"]:
        got = la.verify_handshake_init(json.loads(case["text"]), app_did=hs["app"]["did"], link=hs["link"],
                                       now_ms=hs["now_ms"])
        assert (got.ok, got.reason) == (False, case["reason"]), case["name"]
    for case in hs["accept_rejects"]:
        got = la.verify_handshake_accept(json.loads(case["text"]), node_did=hs["node"]["did"], client_pubkey=r.pubkey,
                                         link=hs["link"], now_ms=hs["now_ms"])
        assert (got.ok, got.reason) == (False, case["reason"]), case["name"]


def test_packets():
    p = V["packets"]
    assert p["limits"] == sp.LIMITS and p["max_packet_bytes"] == sp.MAX_PACKET_BYTES
    for case in p["good"]:
        assert sp.decode(case["text"].encode("utf-8")) == json.loads(case["text"])
    for case in p["bad"]:
        with pytest.raises(sp.PacketError) as e:
            sp.decode(case["text"].encode("utf-8"))
        assert (e.value.path, e.value.rule) == (case["path"], case["rule"]), case["name"]
    for case in p["not_canonical"]:
        with pytest.raises(sp.PacketError):
            sp.decode(case["text"].encode("utf-8"))
