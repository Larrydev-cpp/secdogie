"""Sealed input: a request's prompt, readable only by the model nodes it names.

Only one participant in the mesh ever needs the prompt: the model node that
passes it to its upstream API. Deciders route on the request's models, scopes,
quorum and pins; forwarders and the coordinator carry frames; the safety layer
judges answers. None of them reads the input. So the user seals it to the model
nodes that may answer, and everything else carries ciphertext -- including the
evidence that floods with an incident, which used to show every node and user
the prompt of whoever was involved.

The envelope (``request["sealed"]``, shape checked in ``protocol.sealed_shape``)::

    {"v": 1,
     "nonce": b64(24 bytes),
     "ct":    b64(XChaCha20-Poly1305(K, nonce, utf8(input), aad)),
     "to":    [[model node DID, b64(SealedBox(node's X25519 key, K))], ...]}

``K`` is a fresh 32-byte key per request. The X25519 keys come from the
broker-signed leases (``seal_key``): the broker took each from the node's
verified DID binding, the same key every lease grant is sealed to.

The associated data binds the ciphertext to the request's ``user`` and
``req_id``. Without it, a user who saw another user's sealed request -- in an
incident's evidence, say -- could paste its envelope into a request of their
own and have a model node answer *them* about someone else's prompt. With it,
the pasted ciphertext no longer opens. Everything else about the request is
covered by the user's signature.

Limits, stated plainly:

  * the **answer** still travels in the clear. It has to: answers are the
    evidence that equivocation, key leaks and forbidden proposals are proven
    from, by anyone, without a key. An answer can echo its prompt;
  * every recipient can read the input, which is the point -- a request sealed
    to several model nodes (quorum, or so the decider has a choice) is read by
    each of them;
  * which model nodes a request is sealed to, its models and its size remain
    visible to the carriers.
"""
from __future__ import annotations

import base64
import secrets

from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt as _aead_open
from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt as _aead_seal
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox
from secdogie_identity.signing import canonical

from .protocol import MAX_INPUT, MAX_RECIPIENTS, SEAL_NONCE, SEALED_INPUT, is_did, is_x25519


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _aad(user: str, req_id: str) -> bytes:
    return canonical({"type": SEALED_INPUT, "user": user, "req_id": req_id})


def seal_input(text: str, *, user: str, req_id: str, recipients) -> dict:
    """Seal ``text`` to ``recipients`` -- ``(DID, base64 X25519 key)`` pairs.
    A DID named twice must name the same key."""
    keys: dict[str, str] = {}
    for did, pub in recipients:
        if not is_did(did) or not is_x25519(pub):
            raise ValueError(f"bad recipient: {did!r}")
        if keys.setdefault(did, pub) != pub:
            raise ValueError(f"two keys for one recipient: {did}")
    if not 1 <= len(keys) <= MAX_RECIPIENTS:
        raise ValueError(f"sealed input names 1..{MAX_RECIPIENTS} model nodes")
    key, nonce = secrets.token_bytes(32), secrets.token_bytes(SEAL_NONCE)
    ct = _aead_seal(text.encode("utf-8"), _aad(user, req_id), nonce, key)
    to = [[did, _b64(SealedBox(PublicKey(base64.b64decode(pub))).encrypt(key))] for did, pub in sorted(keys.items())]
    return {"v": 1, "nonce": _b64(nonce), "ct": _b64(ct), "to": to}


def open_input(request: dict, did: str, private_key: PrivateKey | None) -> tuple[str | None, str | None]:
    """The input of a well-formed ``request``, as model node ``did`` can read
    it: ``(text, None)``, or ``(None, why not)``. A request in the clear just
    gives its input."""
    sealed = request.get("sealed")
    if sealed is None:
        return request["input"], None
    wrap = next((w for d, w in sealed["to"] if d == did), None)
    if wrap is None:
        return None, "the input is not sealed to this node"
    if private_key is None:
        return None, "this node holds no key to open sealed input"
    try:
        key = SealedBox(private_key).decrypt(base64.b64decode(wrap))
        plain = _aead_open(base64.b64decode(sealed["ct"]), _aad(request["user"], request["req_id"]),
                           base64.b64decode(sealed["nonce"]), key)
        text = plain.decode("utf-8")
    except (CryptoError, ValueError, TypeError, UnicodeDecodeError):
        return None, "the sealed input does not open"
    if len(text) > MAX_INPUT:
        return None, "input too long"
    return text, None


__all__ = ["seal_input", "open_input"]
