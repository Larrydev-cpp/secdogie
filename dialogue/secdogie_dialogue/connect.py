"""One way to open the App's dialogue session with a node.

Every App wires the same chain -- the ``secdogie-dialogue connect`` command,
the secdogie window's own node, and the nodes the window pairs with:

    UDPChannel -> DirectUDPTransport (trusting exactly one node DID)
      -> [FailoverTransport through relays] -> ChannelMux -> SessionRouter
      -> DialogueSession -> AppController

``open_session`` builds it once, here. Where the node is, in order: an
address given (``node_addr``); else a rendezvous lookup by its DID; else only
the relays. The node's DID is the whole trust set, for the transport and for
the dialogue session alike, so nothing else is heard.
"""
from __future__ import annotations

from dataclasses import dataclass

from secdogie_identity import Allowlist

from .app import AppController
from .session import DialogueSession, SessionRouter


class NodeNotFound(LookupError):
    """No address for the node: not given, not at any rendezvous, no relay."""


@dataclass
class AppLink:
    """An open session with one node: the controller the App drives, and what
    ``close()`` tears down."""

    controller: AppController
    channel: object
    transport: object
    relay_link: object = None
    finder: object = None

    def close(self) -> None:
        try:
            self.controller.close()
        finally:
            for part in (self.relay_link, self.finder):
                if part is not None:
                    part.close()
            self.channel.close()


def open_session(identity, node_did: str, *, listen: tuple[str, int] = ("0.0.0.0", 0),
                 node_addr: tuple[str, int] | None = None, rendezvous=(), relays=(), transport_key=None,
                 binding: dict | None = None, start: bool = True) -> AppLink:
    """Open the App's session with ``node_did``. ``identity`` is the App's
    session key. Raises ``ValueError`` for a bad binding or record,
    ``NodeNotFound`` when there is nowhere to send, and ``ImportError`` without
    the transport package. With ``start`` the session starts and says hello."""
    from secdogie_transport import (
        ChannelMux,
        DirectUDPTransport,
        Endpoint,
        FailoverTransport,
        PeerIdentity,
        RendezvousLink,
        Session,
        UDPChannel,
    )

    trust = Allowlist({node_did})
    channel = UDPChannel(*listen)
    link = finder = None
    try:
        transport = DirectUDPTransport(identity, channel, allowlist=trust, transport_key=transport_key)
        if binding is not None and not (transport.add_peer_binding(binding) and binding.get("did") == node_did):
            raise ValueError("--node-binding is not a valid binding for --node")
        if node_addr:
            transport.set_peer_endpoint(node_did, *node_addr)
        if rendezvous:
            finder = RendezvousLink.from_records(transport, list(rendezvous))
            found = finder.lookup(node_did)
            if found is not None:
                best = found.best()
                transport.set_peer_endpoint(node_did, best.host, best.port)
            elif not node_addr and not relays:
                raise NodeNotFound(f"the node {node_did} is not registered at any rendezvous given")
        elif not node_addr and not relays:
            raise NodeNotFound(f"no address for the node {node_did}: give one, a rendezvous or a relay")
        carrier = transport
        if relays:
            link = carrier = FailoverTransport.from_records(transport, list(relays))
            link.start()
        mux = ChannelMux(carrier, Session("dialogue-app", PeerIdentity(identity.did, ""),
                                          active=Endpoint("local", *channel.address)))
        router = SessionRouter(mux)
        session = router.add(DialogueSession(identity, node_did, router.sender_for(node_did), trust=trust))
        controller = AppController(session)
    except BaseException:
        for part in (link, finder):
            if part is not None:
                part.close()
        channel.close()
        raise
    app_link = AppLink(controller, channel, transport, link, finder)
    if start:
        session.start(0.05)
        controller.start()
    return app_link


__all__ = ["AppLink", "NodeNotFound", "open_session"]
