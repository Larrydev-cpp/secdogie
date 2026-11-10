"""The address book that replaces seed nodes: verified hints only, two tiers,
identities that cost work, network diversity, room by behaviour, and a peer
exchange that never tells a public peer where your own nodes are."""
from __future__ import annotations

from secdogie_identity import Identity
from secdogie_transport.endpoint import Endpoint
from secdogie_transport.membership import sign_record
from secdogie_transport.peerbook import (
    OWN,
    PUBLIC,
    PeerBook,
    _puzzle_hash,
    prefix_of,
    puzzle_ok,
    solve_puzzle,
)

D = 8  # a quick puzzle for tests; the default costs about a second
ME = Identity.generate()
MINE = Identity.generate()


def _record(ident, host, port=4000, t=100.0):
    return sign_record(ident, [Endpoint("public", host, port)], last_seen=t)


def _public(host, t=100.0):
    ident = Identity.generate()
    return ident, _record(ident, host, t=t), solve_puzzle(ident.did, D)


def _book(**kw):
    return PeerBook(ME.did, own={MINE.did}, public=True, difficulty=D, **kw)


def test_by_default_the_book_keeps_only_your_own_devices():
    book = PeerBook(ME.did, own={MINE.did}, difficulty=D)
    _, rec, n = _public("198.51.100.7")
    assert book.admit(rec, nonce=n) is None  # a solved puzzle is not enough: strangers are not kept
    assert book.admit(_record(MINE, "10.0.0.2")) == OWN
    assert book.merge([{"record": rec, "nonce": n}]) == 0
    assert book.dids() == [MINE.did]


def test_the_identity_puzzle_costs_work_and_is_bound_to_its_did():
    a, b = Identity.generate(), Identity.generate()
    n = solve_puzzle(a.did, 12)
    assert puzzle_ok(a.did, n, 12) and not puzzle_ok(a.did, n, 40)
    assert _puzzle_hash(a.did, n) != _puzzle_hash(b.did, n)  # the work is bound to its DID
    assert not puzzle_ok(a.did, -1, 12) and not puzzle_ok(a.did, "7", 12)


def test_hints_are_admitted_only_when_they_verify():
    book = _book()
    stranger, rec, nonce = _public("203.0.113.5")
    assert book.admit(rec, nonce=nonce) == PUBLIC
    assert book.admit(_record(MINE, "10.0.0.2")) == OWN  # your own nodes need no puzzle
    assert book.admit(_record(ME, "10.0.0.1")) is None  # never ourselves
    assert book.admit({**rec, "endpoints": [{"kind": "public", "host": "6.6.6.6", "port": 1}]}, nonce=nonce) is None
    other, rec2, _ = _public("198.51.100.7")
    assert book.admit(rec2, nonce=0) is None or puzzle_ok(other.did, 0, D)  # no work, no entry
    assert book.admit(_record(stranger, "203.0.113.5", t=50.0), nonce=nonce) is None  # older than what is held
    assert book.admit(_record(stranger, "203.0.113.9", t=200.0), nonce=nonce) == PUBLIC  # newer wins
    assert [e.host for e in book.get(stranger.did).record.endpoints.all()] == ["203.0.113.9"]


def test_one_network_cannot_fill_the_book():
    book = _book(per_prefix=2)
    admitted = [book.admit(rec, nonce=n) for _, rec, n in (_public(f"203.0.113.{i}") for i in range(1, 6))]
    assert admitted.count(PUBLIC) == 2 and admitted.count(None) == 3
    assert prefix_of("203.0.113.77") == "203.0.113.0/24" and prefix_of("2001:db8:1:2::5") == "2001:db8:1::/48"


def test_room_goes_by_behaviour_then_age():
    book = _book(capacity=2, per_prefix=10)
    a, ra, na = _public("192.0.2.1", t=100.0)
    b, rb, nb = _public("192.0.2.2", t=110.0)
    assert book.admit(ra, nonce=na) and book.admit(rb, nonce=nb)
    c, rc, nc = _public("192.0.2.3", t=90.0)
    assert book.admit(rc, nonce=nc) is None  # full of well-behaved, fresher peers
    book.failed(b.did)
    assert book.admit(rc, nonce=nc) == PUBLIC and book.get(b.did) is None  # the failing one made room
    for _ in range(5):
        book.failed(a.did)
    assert book.get(a.did) is None  # a public peer that keeps failing is dropped
    book.admit(_record(MINE, "10.0.0.2"))
    for _ in range(9):
        book.failed(MINE.did)
    assert book.get(MINE.did) is not None  # your own nodes are never dropped for it


def test_peer_exchange_spreads_the_mesh_and_never_reveals_your_own_nodes():
    alice, bob = _book(), PeerBook(Identity.generate().did, public=True, difficulty=D)
    for i in range(3):
        _, rec, n = _public(f"198.51.{i}.10")
        alice.admit(rec, nonce=n)
    alice.admit(_record(MINE, "10.0.0.2"))
    offered = alice.offer(PUBLIC)
    assert MINE.did not in {o["record"]["did"] for o in offered}  # a public neighbour never learns your machines
    assert bob.merge(offered) == 3 and len(bob) == 3
    assert MINE.did in {o["record"]["did"] for o in alice.offer(OWN)}  # your own nodes share everything
    assert bob.merge([{"record": {"type": "junk"}}, "x", {"record": offered[0]["record"], "nonce": -1}]) == 0


def test_the_book_survives_a_restart_and_is_verified_again(tmp_path):
    book = _book()
    s, rec, n = _public("203.0.113.5")
    book.admit(rec, nonce=n)
    book.admit(_record(MINE, "10.0.0.2"))
    book.succeeded(s.did)
    path = tmp_path / "peers.json"
    book.save(path)
    again = _book()
    assert again.load(path) == 2 and again.get(s.did).successes == 1
    tampered = path.read_text().replace("203.0.113.5", "6.6.6.6")
    path.write_text(tampered)
    assert _book().load(path) == 1  # the altered record no longer verifies; your own still does
